> ⚠️ **免责声明**：本项目仅为个人学习与技术验证用途，不构成任何商业产品。详见 [DISCLAIMER.md](./docs/DISCLAIMER.md)。

# i-member · 多 Agent 智能品牌伙伴

旨在建立依托品牌全渠道 SCRM 系统，构建以 Agent 为核心的**私域会员智能运营中枢**，连接"消费者交互"、"业务工作流"与"数据洞察"，实现从会员对话到工单自动化处理的闭环。[终极愿景](https://github.com/bonnie2015/i-member/issues/1)

## 当前版本

**Milestone 1: 核心在线服务能力落地** — 已完成 Router、QA、Ticket、Recommend、Post Process 的核心链路，可运行、可展示。

## 核心架构

```
用户消息 → FastAPI → invoke_member_ops
                         │
                    _build_invoke_input
                    (中断检测 / 上下文加载)
                         │
                    wf.ainvoke()
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
           router     ticket     qa/recommend
         (意图分类)  (子图)      (节点)
              │
    ┌─────ticket─────┐    ┌──qa──┐  ┌──recommend──┐
    │ guard → plan   │    │ RAG  │  │ guard →     │
    │  → executor    │    │ 检索 │  │  search →    │
    │  → reflect     │    │ 回答 │  │  recommend   │
    │  → finalize    │    └──────┘  └──────────────┘
    └────────────────┘
         │
    post_process (后台异步)
    ├─ service_summary (ollama)
    └─ user_facts (deepseek)
```

主图负责意图路由和中断恢复。三个子图分别为[工单模块](https://github.com/bonnie2015/i-member/issues/3)、[推荐模块](https://github.com/bonnie2015/i-member/issues/9)、[咨询模块](https://github.com/bonnie2015/i-member/issues/3)。工单模块是最复杂的部分，五节点状态机 + 独立 interrupt 节点实现完整闭环。所有状态通过 Redis checkpoint 持久化。

## 技术栈

LangGraph · LangChain · FastAPI · Redis · Qdrant · DeepSeek API · Ollama (qwen2.5:7b) · Python asyncio

基于 LangGraph 的多 Agent 架构，支持工单办理、知识问答、商品推荐三种业务场景的智能客服系统。LLM DeepSeek + Ollama 混合部署，Redis 状态持久化。

## 面向品牌的可插拔设计

- 所有核心流转不涉及实际业务，旨在面向不同品牌高可复用。
- 业务数据与品牌信息通过独立的技能文件、提示词前缀和工具层分离，不同品牌只需进行少量业务兼容即可快速落地。
- 接入方式详见下方"接入新品牌"。

## 效果展示

| 场景 | 详情 | 特性 |
|------|------|------|
| **工单办理** | [#3](https://github.com/bonnie2015/i-member/issues/3#issuecomment-4405109262) | 交互卡片选择订单 → 确认商品 → 收集原因 → 建单成功 → 自动嵌入工单确认卡片，全链路闭环 |
| **商品推荐** | [#9](https://github.com/bonnie2015/i-member/issues/9#issuecomment-4405059807) | 春季鲜亮色偏好收敛 → 酒红色 MEXICO 66 SD 选定 → 搭配黄色 T 恤 + 连衣裙下单 |
| **知识问答** | [#11](https://github.com/bonnie2015/i-member/issues/11#issuecomment-4405130128) | 隐私政策 RAG 检索 → 用户纠正回答 → 模型自我修正 → 服务切换至工单查询 |

> ⚠️ 以上 Issue 中的截图包含的品牌相关示例数据，**仅供技术演示用途**，不代表真实商业交易或客户信息。

## 性能表现

- **工单场景**：单轮用户回复处理 2-6s，首响约 11s。优化后单轮 token 稳定在 2.7k-4.8k，实际完整服务结束与用户回复、步骤规划方式强相关。[详细数据](https://github.com/bonnie2015/i-member/issues/15#issuecomment-4402489608)
- **推荐场景**：单次推荐响应 5-8s，含商品搜索 1-2 次 LLM 调用，token ~3.5k。[详细数据](https://github.com/bonnie2015/i-member/issues/15#issuecomment-4402895154)
- **咨询场景**：RAG 检索 + 回答 5-7s，压缩比约 43%，压缩后回答质量无退化。[详细数据](https://github.com/bonnie2015/i-member/issues/15#issuecomment-4403400695)

> 以上为当前测试环境下的粗粒度数据，实际表现与具体业务场景和接入数据相关，正式接入后需要进一步精调。

## 工程设计

> [完整版](./docs/ENGINEERING.md)

### 上下文管理

- **跨服务记忆**：服务切换时清空运行时状态，保留最后一轮对话和摘要，避免上下文膨胀
- **try_process 审计追踪**：工具调用链作为单一数据源，支持 token 自动压缩和断点恢复
- **技能渐进披露**：按阶段分标签加载 SKILL.md，replan 时 prompt 减半
- **各子图差异化策略**：qa 按 token 压缩对话；推荐 guard 抽取锚点商品；replan 回传 try_process

### 用户体验

- **中断恢复**：三版迭代，手动 ReAct + 独立 interrupt 节点，checkpoint 原生覆盖
- **结构化交互卡片**：select（可选）/ confirm（只展示），建单自动嵌入确认卡片
- **多 tool_call 支持**：LLM 一次返回多个工具时逐个执行逐个应答

### Agent 协作与自愈

- **手动 ReAct 循环**：精确控制工具调用上限，动态缩减可用工具
- **replan 自愈**：步骤失败回溯 try_process 重新规划，最多 2 次
- **事实提取**：casefold 去重，支持 add + delete 双向变更

## 品牌接入指南

> ⚠️ 接入灵活度目前处于 **Beta 阶段**，接入后需根据实际情况再次调测。

本仓库作为实例框架，不上传实际品牌文件。如需接入一个品牌，需要准备以下内容：

**1. 品牌提示词**（`app/prompts/prompt_prefix.txt`）

定义品牌人设、语气、经营范围。一个文件即可，所有 prompt 自动继承。

**2. 技能文件**（`app/skills/ticket/{skill-name}/SKILL.md`）

按业务场景编写。每个 SKILL.md 包含：
- `name` / `description` / `available_tools` / `clarify_labels`（YAML frontmatter）
- 核心定位、受理条件、处理原则、场景细分、工具使用时机（Markdown body）

**3. 业务工具**（`app/tools/business/`）

实现 SCRM API 的调用封装，返回标准化 JSON。需提供：
- 工具函数（`@tool` 装饰器）
- 输入 schema（Pydantic Field）
- API 调用与结果归一化

**4. 推荐系统工具**（如适用）

商品搜索、详情查询、尺码指南等，同上封装。

**5. Mock 数据**

准备测试用户、订单、商品、工单的模拟数据集，供本地开发和效果展示使用。
