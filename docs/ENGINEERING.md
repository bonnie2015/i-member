# 工程设计亮点

## 上下文管理

### 跨服务记忆与对话延续

服务结束后只保留最后一轮对话消息，其余清空——既避免多轮服务的对话膨胀，又保留足够上下文让下一个服务理解用户意图。同时写入服务记忆摘要（Redis list，TTL 2 天，最多 10 条），支持跨会话的服务连续性。用户下次进入时，`load_user_context` 从 Redis 拉取服务记忆和用户事实注入 prompt，模型看到的是"这个人之前退货过某款鞋 37 码"，而不是半年前的完整聊天记录。

### try_process 审计追踪

当前步骤的工具调用链作为单一数据源，所有工具结果存储结构化原始数据而非字符串。system prompt 和 messages 不再双重嵌入同一份数据，token 从源头减半。不依赖上下文变量（ContextVar），改用 module-level dict 保证工具结果在 interrupt 恢复后仍然可见。

try_process 同时承担三个角色：
- **压缩**：超 token 阈值自动将旧条目压缩为摘要，保留最近一对完整记录
- **恢复**：中断恢复时从 try_process 还原对话消息，`pending_tool_call_id` 桥接保证 tool_call_id 配对
- **回放**：replan 时将完整 try_process 回传给 planner，使重规划基于真实工具返回结果而非推测

### Token 感知的自动压缩

基于 DeepSeek 官方换算公式（中文字符×0.6 + 英文字符×0.3）实时估算 token，超阈值自动压缩。格式统一：所有工具 `{tool/args}` + `{tool/result}` 成对写入，ask_user 也不例外。压缩后旧条目为 `{compressed: "摘要"}`，最近一对完整保留。

### 技能渐进披露

按流程阶段分标签加载 SKILL.md（guard/plan_first/plan_replan）。guard 只需知道"能做什么"，plan 需要完整流程，replan 时砍掉已传达过的场景流程描述。prompt 减半但关键约束完整保留，省 3-4s 且不飘。execute 不看技能文件，有独立 prompt。

### qa 对话压缩

对话记录和 RAG 检索结果按 token 阈值触发压缩，而非固定轮数。`estimate_tokens` 统一估算，超过 `_MAX_QA_TOKENS` 时调 `summary_agent.compress_qa()` 将历史对话压缩为摘要。

### 推荐 guard 摘要

recommend 子图独有的 guard 节点，专门做累积摘要与锚点商品抽取。将多轮推荐历史（可达 20 轮）压缩为一段摘要 + 若干锚点商品，下一轮只传摘要而非完整历史，既控制了上下文规模又保留了用户偏好的关键商品信息。

## 中断恢复与用户体验

### 中断恢复方案演进

从内嵌 `create_react_agent` 到传父图 checkpointer 到手动 ReAct 循环 + 独立 interrupt 节点，三版迭代：

- **v1**：`create_react_agent` 默认配置 → 中断在子图的子图，父图看不到
- **v2**：传父图 checkpointer → `ainvoke` 是独立运行，resume 时 checkpoint 丢失
- **v3**：手动 ReAct 循环 + 独立 `executor_interrupt_node` → 中断点提升到 ticket_subgraph 节点级别，父图 checkpoint 原生覆盖

最终方案：executor_node 手动 ReAct 循环中检测 ask_user → 写 try_process 并返回 → `_route_after_executor` 路由到 `executor_interrupt_node` → 调用 `graph_interrupt(payload)` → 父图 checkpoint 覆盖中断点 → 恢复后 `executor_interrupt_node` 将用户回复写入 try_process → 路由回 executor 继续执行。

### 结构化交互卡片

订单/商品/工单的 select（可点击选择，`selectable=true`）与 confirm（只展示确认，`selectable=false`）两种模式。用户点选代替打字，减少无效追问轮次。

卡片数据流：SCRM/Onitsuka 工具执行 → `push_ticket_interaction_source` 推送至 module-level dict → ask_user 调 `_normalize_interaction` 从 dict 提取实体 → 构建 `InteractionPayload` → 前端渲染卡片。建单成功后自动嵌入工单确认卡片，和追问卡片同一字段返回，前端零改动。

### 多 tool_call 支持

LLM 一次返回多个工具调用时逐个执行逐个应答，消除 DeepSeek API 的 400 错误（"tool_calls must be followed by tool messages"）。先扫描所有 tool_calls 检测 ask_user/finish_step，无则全部执行。

## Agent 协作与自愈

### 手动 ReAct 循环

替代 `create_react_agent` 黑盒，精确控制工具调用上限（`_MAX_TOOL_CALLS`）。接近上限时动态缩减可用工具至 `[finish_step, ask_user]`，强制结束步骤。try_process 作为 transaction log 支持断点恢复。所有工具结果原样存储，不依赖 ContextVar。

### replan 自愈机制

步骤执行失败或超上限时，reflect 携带完整 try_process 触发 planner 重规划。planner 基于实际工具返回结果而非推测重新规划路径，最多 2 次。新 goal 包含"此前尝试了什么"的简短摘要，executor 拿到后知道该绕开什么、该接着哪个结果继续。

### 事实提取与去重

user_facts 基于 casefold 去重，支持 `add_facts` + `delete_facts` 双向变更。最多保留 8 条核心事实，TTL 30 天。每次服务结束后异步提取，LLM 用 deepseek，提取结果与已有事实合并后写回 Redis。

## 后处理链路

双后台任务（`spawn_post_process_tasks`），服务完成后异步执行：

- **服务记忆**：`summarize_service(messages, intent)` → ollama → Redis list（TTL 2 天，最多 10 条）。intent 差异化 payload（ticket 含 steps/slots/final_status，recommend 含 trace）
- **用户事实**：`extract_and_save_user_facts(messages)` → deepseek → Redis（TTL 30 天，最多 8 条）

调用时机：主图检测 `service_finished` 时或 Router 检测 QA→其他服务切换时。

## LLM 基础设施

- **模型路由**：`_ROLE_PROVIDER` 字典按角色分配 local/remote，`_get_local_llm` 和 `_get_remote_llm` 自动路由。按任务特点分流：plan/executor 走 deepseek（质量优先），summary/compress 走 ollama（降本）
- **Token 估算**：`estimate_tokens(text)` — DeepSeek 官方换算公式，统一用于 executor 压缩、qa 压缩、RAG 压缩
- **可观测性打点**：`invoke_with_usage_logging` 结构化 JSON 日志（node、provider、model、latency_ms、tokens）
- **Agent 超时降级**：`BaseAgent.run()` 统一超时/异常/递归限制的兜底

## 工程契约

- **AgentInput/Output 统一契约**：所有 Agent 通过 `AgentInput(extra={...})` 传递上下文
- **Checkpoint 容错**：`create_checkpointer()` 优先 Redis AsyncRedisSaver（7 天 TTL），fallback MemorySaver
- **多品牌可插拔**：业务逻辑与品牌信息通过技能文件、提示词、工具层分离，不同品牌替换对应文件即可适配
