# Member-Ops Agent 项目文档

## 目录

- [1. 项目概述](#1-项目概述)
- [2. 技术架构](#2-技术架构)
- [3. 目录结构](#3-目录结构)
- [4. 核心设计决策](#4-核心设计决策)
- [5. 记忆系统](#5-记忆系统)
- [6. 可靠性设计](#6-可靠性设计)
- [7. Token 成本控制](#7-token-成本控制)
- [8. API 文档](#8-api-文档)
- [9. 配置说明](#9-配置说明)
- [10. 部署指南](#10-部署指南)
- [11. 待实现功能](#11-待实现功能)

***

## 1. 项目概述

全渠道会员智能运营 Agent，以 LangGraph 为核心，连接消费者交互、业务工作流与数据洞察，实现从会员对话、工单自动化处理到品牌经营策略进化的全链路闭环。

### 核心功能模块

**模块一：私享品牌伙伴（实时交互）**

- 会员全视图咨询：识别会员身份，调取跨渠道历史，提供差异化回复
- 智能工单流转：识别退换货/破损/投诉/权益申请等诉求，自动提取信息，创建工单并追踪
- 情感安抚：实时情绪评分，负面情绪触发安抚话术，VIP 触发补偿券
- 主动触达：行为触发营销（加购未支付）、生命周期自动化（沉默召回）
- 对话转化：识别购买意图，生成个性化推荐与优惠

**模块二：数据智能闭环（离线分析）**

- 会员数据治理：多渠道数据规范化
- 品牌洞察引擎：基于 topic\_tags 批量聚类，输出产品报告与经营建议

### 技术选型

| 组件       | 选型                    | 理由                                |
| -------- | --------------------- | --------------------------------- |
| Web 框架   | FastAPI + Pydantic    | 异步支持，类型校验                         |
| Agent 引擎 | LangGraph             | 状态图管理，原生 Checkpointer / interrupt |
| 路由模型     | Ollama + Qwen2.5:3b   | 本地推理，低延迟，JSON mode                |
| 业务模型     | DeepSeek-chat         | 强推理，多轮对话                          |
| 工作记忆     | Redis AsyncRedisSaver | LangGraph 原生对话状态持久化               |
| 长期记忆     | Redis asyncio         | 跨会话服务历史                           |
| SCRM 缓存  | Redis asyncio         | 用户画像与行为轨迹短期缓存                     |
| 限流       | slowapi + Redis       | 入口限流，防雪崩                          |
| 重试       | tenacity              | 外部调用容错                            |
| 熔断       | circuitbreaker        | 下游保护                              |

**明确不引入（及原因）**

- Weaviate：运维成本高，中小规模知识库用 pgvector 足够；洞察引擎是离线任务，第二阶段再评估
- Plan-and-Execute-Reflect（完整离线版）：实时对话场景 LLM 调用次数约 7 次（vs ReAct 约 3 次），延迟不可接受；保留用于离线洞察引擎。**注意：service 子图使用轻量版 P\&E-Reflect（Reflect 仅在用户交互间隙触发，不额外增加实时延迟）**

***

## 2. 技术架构

### 2.1 整体架构

```
用户（微信 / 小程序 / 官网 / 电商平台 / API）
          │
          ▼
    [入口限流] slowapi 20次/分钟/IP
          │
          ▼
    FastAPI API 网关
          │
          ▼
    ┌─────────────┐
    │   Router    │  Qwen2.5:3b 意图分类（仅当前消息，仅首轮）
    └──────┬──────┘
           │
    ┌──────┼──────────────────┐
    ▼      ▼                  ▼
service  qa_block        recommend_block
(P&E-    (ReAct Agent    (ReAct Agent
 Reflect) DeepSeek)       DeepSeek)
 ↕interrupt多轮  ↕interrupt多轮  ↕interrupt多轮
    │      │                  │
    └──────┴──────────────────┘
                  │
                  ▼
           post_process
    （记忆写入 / 情绪补偿 / 待提醒任务入队 / intent_queue 路由）
                  │
         ┌────────┴────────┐
         ▼                 ▼
   intent_queue 非空    intent_queue 空
   → 直接路由下一子图        → END
```

### 2.2 工作流图（LangGraph StateGraph）

```
用户输入
   │
   ▼
router（仅首轮运行）
   └── 意图识别（Qwen2.5:3b，仅当前消息）
              ├── service   → service_block（P&E-Reflect）
              │                  ├── [interrupt] 需要更多信息 / 等待确认 ←── 用户输入恢复
              │                  └── 工单创建成功 / 用户取消 → post_process
              ├── qa        → qa_block（ReAct）
              │                  ├── [interrupt] 回复用户，等待追问 ←── 用户输入恢复
              │                  └── 服务结束判断通过 → post_process
              └── recommend → recommend_block（ReAct）
                                 ├── [interrupt] 回复用户，等待追问 ←── 用户输入恢复
                                 └── 推荐完成 → post_process

post_process
   ├── intent_queue 非空 → 清空 messages → 路由到下一子图（不经 router）
   └── intent_queue 空  → END
```

**多轮机制**：三个子图均通过 `interrupt()` 实现多轮。图执行暂停在 interrupt 处，checkpointer 快照当前状态；用户下一条消息恢复图执行，无需重走 router。router 在整个服务周期内只运行一次（首条消息）。

**服务结束判断**：

| 子图        | 结束依据                        |
| --------- | --------------------------- |
| service   | 工单创建成功 或 用户明确取消             |
| recommend | 推荐内容交付完成，用户无进一步追问           |
| qa        | 轮次超阈值且连贯性检测不连贯，或达到强制上限 15 轮 |

**QA 服务结束判断**（两阶段）：

1. `qa_turn_count < 5`：继续，interrupt 等待追问
2. `qa_turn_count >= 5`：用 Qwen 做连贯性二分类
   - 不连贯 → 退出子图 → post\_process
   - 连贯 → 继续，下轮再检测
3. `qa_turn_count >= 15`：无条件强制结束

**多意图串行执行（intent\_queue）**：

router 一次性识别所有意图，主意图立即进入对应子图，次意图写入 `intent_queue`。当前子图完成后，post\_process 弹出队首，**清空 messages**，直接路由到下一子图，用户无感知切换。子图内检测到意图切换时同样追加队列，两类场景统一处理。

### 2.3 State 设计（扁平结构）

**设计原则**：LangGraph 对嵌套 TypedDict 使用浅合并，嵌套结构会导致字段被整体替换而非合并。所有字段必须扁平展开，`add_messages` reducer 才能正确工作。

```python
class AgentState(TypedDict, total=False):
    # 会话身份
    user_id: str
    thread_id: str   # 用户级长期 ID（user_id 即 thread_id），不再随服务重置
    channel: Literal["wechat", "app", "web", "jd", "tmall", "douyin", "api"]

    # 对话消息（add_messages reducer 自动追加，不可嵌套）
    # 每次进入新子图前由 post_process 清空，只保留当前服务的对话
    messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]

    # 路由元数据（不再参与流控，仅供 post_process 判断服务类型）
    intent: Optional[str]
    confidence: Optional[Dict]
    reason: Optional[str]

    # QA 专用
    qa_turn_count: int
    service_entry_message: Optional[str]  # 首条消息，连贯性检测基准

    # 意图队列（多意图串行执行，最多3个）
    # 覆盖两类场景：
    #   1. 初始消息含多意图 → router 一次性写入 ["recommend", "qa"]
    #   2. 服务中途意图切换 → 子图追加到队列
    # post_process 结束时 pop 队首，清空 messages，直接路由下一子图
    intent_queue: List[str]  # 最大长度 3，由 prompt 约束，超出部分丢弃

    # 业务数据（子图写入，post_process 读取）
    emotion_score: Optional[float]   # 0=极负面，1=极正面，各轮二分类累积均值
    pending_task: Optional[Dict]     # 待跟进任务，仅限 ticket/restock 两类
```

**子图 State（扁平继承）**：

```python
class ServiceState(AgentState, total=False):
    # service 子图私有字段，执行完后不写回父图
    ticket_type: Optional[Literal["refund", "change", "quality", "complain", "equity"]]
    extracted_order_id: Optional[str]
    extracted_issue: Optional[str]
    ticket_id: Optional[str]
    department: Optional[str]
    info_complete: bool
    retry_count: int
```

子图 State 继承父图 State 并追加私有字段（保持扁平）。LangGraph 在父子图边界只传递父图 schema 中存在的字段，私有字段自动隔离。

### 2.4 LLM 工厂

```python
# harness/agents/llm/llm_factory.py

@lru_cache(maxsize=4)
def get_local_llm(role="router", format=None) -> BaseChatModel:
    # ChatOllama(qwen2.5:3b), temperature=0, 可选 format="json"
    # lru_cache 保证相同参数全局复用同一实例（单例）
    # 延迟 import：避免未安装包时导致整个模块加载失败

@lru_cache(maxsize=4)
def get_remote_llm(role="qa") -> BaseChatModel:
    # ChatDeepSeek(deepseek-chat), temperature=0.3
    # deepseek_api_key 未配置时 RuntimeError，明确报错不静默降级
```

**单例机制**：`@lru_cache` 以参数为 key 缓存实例。`ChatOllama`/`ChatDeepSeek` 是无状态对象（只持有配置，每次 ainvoke 独立 HTTP 请求），多并发请求共享同一实例安全。

### 2.5 Checkpointer 初始化

**问题根因**：`AsyncRedisSaver` 底层用 Redis Search，使用前必须调用 `await setup()` 建立 `checkpoint` 和 `checkpoint_write` 两个索引。在模块导入时同步初始化，`setup()` 从未被调用，多轮对话时报 `No such index checkpoint_write`。

**解法**：通过 FastAPI lifespan 在服务启动时异步完成初始化：

```python
# app/main.py
@asynccontextmanager
async def lifespan(app):
    checkpointer = await create_checkpointer()       # ping → setup() 建索引
    graph_module.workflow = graph_module.create_workflow(checkpointer)
    yield

# harness/agents/memory/redis_checkpointer.py
async def create_checkpointer():
    # AsyncRedis.ping() → AsyncRedisSaver → await setup()
    # 失败时降级 MemorySaver（开发/测试可用）
```

`graph_module.workflow = None` 占位，lifespan 赋值后所有请求才进入。**子图编译时不传 checkpointer**，只在父图传，父图 checkpointer 自动快照包含子图的完整 state。

### 2.6 Thread ID 生命周期

**核心设计**：一次服务 = 一个 thread\_id，由 API 入口在收到请求时生成。

```
服务1: 请求不含 thread_id → 入口生成 thread_id=user123_a1f3 → 子图执行（interrupt多轮）→ post_process
服务2: 请求不含 thread_id → 入口生成 thread_id=user123_b4e2 → 子图执行 → post_process
服务进行中: 请求携带 thread_id=user123_a1f3 → Checkpointer 恢复上次 interrupt 位置 → 继续执行
```

**为什么这样设计**：

- checkpointer 每个 thread 只持有当前服务的 messages，不累积历史
- 新服务新 thread，messages 天然隔离，无需手动清空
- interrupt 多轮期间客户端持续携带同一 thread\_id，图从暂停点恢复

**客户端协议**：首次请求不传 `thread_id`，后端生成并在响应中返回；服务进行中（图暂停在 interrupt）每轮都携带同一 `thread_id`；服务结束后下次请求不传，触发新 thread 生成。

### 2.7 子图实现策略

| 子图        | 实现方式                         | 原因                                                                    |
| --------- | ---------------------------- | --------------------------------------------------------------------- |
| service   | Plan-and-Execute-Reflect（轻量） | 步骤动态（依 ticket\_type 而定），需跨步骤计划修正（步骤失败/用户意图纠正）；Reflect 在用户交互间隙触发，无感知延迟 |
| qa        | `create_react_agent`（ReAct）  | 无固定步骤，工具调用顺序由 LLM 按观察结果决定；每步 think-after-observe 已内嵌 Reflect          |
| recommend | `create_react_agent`（ReAct）  | 同上，需要组合多数据源（行为、偏好、库存）；步骤级隐式 Reflect 已满足需求                             |

**ReAct vs Plan-and-Execute-Reflect**：

| 维度             | ReAct              | P\&E-Reflect（轻量，service） | P\&E-Reflect（完整，离线）      |
| -------------- | ------------------ | ------------------------ | ------------------------ |
| LLM 调用次数（3步工具） | \~3 次（含隐式 Reflect） | \~4-5 次（Reflect 在交互间隙）   | \~7 次（plan+exec+reflect） |
| 实时对话延迟         | 低                  | 低（用户等待期消化 Reflect）       | 高，不可接受                   |
| Reflect 机制     | 隐式：每步 think 即反思    | 显式：跨步骤计划修正               | 显式：完整循环                  |
| 跨步骤计划修正        | 无（步骤失败只影响当步）       | 有（可修正整体计划）               | 有                        |
| 适用场景           | qa / recommend     | service（有中间步骤失败或意图纠正需求）  | 洞察引擎离线分析报告               |

**ReAct 内嵌 Reflect 的原理**：ReAct 每轮的 think 步骤在 observe（工具结果）之后执行，模型在此判断"结果是否符合预期、下一步应该做什么"——这就是 Reflect，只是被压缩进了每一轮的 think 里，无需额外调用。对于 qa/recommend 的工具链（1-3步只读操作），步骤级粒度的隐式 Reflect 已满足需求。

**service 选择 P\&E-Reflect 而非 ReAct 的关键原因**：工单流程有依赖关系（信息收集 → 验证 → 确认 → 创建），若中间步骤失败（API 报错、信息不完整）或用户中途改变意图（"不是退货是投诉"），需要修订整体计划而非仅重试当前步骤。ReAct 的步骤粒度无法支持这种跨步骤计划修正。

***

## 3. 目录结构

```
i-member/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/
│   │   │   └── chat.py               # POST /api/v1/chat
│   │   ├── models/
│   │   │   └── schemas.py            # ChatRequest / ChatResponse
│   │   └── main.py                   # FastAPI app + lifespan（checkpointer 初始化）
│   ├── harness/
│   │   ├── agents/
│   │   │   ├── llm/
│   │   │   │   └── llm_factory.py    # get_local_llm / get_remote_llm（lru_cache 单例）
│   │   │   ├── memory/
│   │   │   │   └── redis_checkpointer.py   # create_checkpointer()（异步工厂）
│   │   │   └── prompts/
│   │   │       ├── router.txt        # 统一入口路由 prompt（输出 JSON）
│   │   │       └── prompt_loader.py  # lru_cache 文件缓存
│   │   ├── config/
│   │   │   ├── config.py             # Settings（Pydantic BaseSettings）
│   │   │   └── logging.py
│   │   └── workflow/
│   │       ├── state.py              # AgentState（扁平）/ ServiceState 等
│   │       ├── graph.py              # create_workflow(checkpointer)，workflow=None 占位
│   │       ├── nodes/
│   │       │   └── router.py         # router_node（统一完成连续性与大类路由）
│   │       └── subgraphs/
│   │           └── service.py        # 工单结构化子图（待实现）
│   ├── logs/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── index.html / style.css / script.js
│   ├── server.js                     # Express 静态服务
│   └── Dockerfile
├── doc/
│   ├── prd.md
│   └── PROJECT.md
├── docker-compose.yml
└── .env
```

***

## 4. 核心设计决策

### 4.1 意图识别

- **触发条件**：`current_subgraph` 为空（无进行中的服务）
- **输入**：**仅当前用户消息，不传历史**
  - 原因：路由是分类任务，不是对话理解；历史会干扰小模型，增加延迟；子图进行中时 router 不运行，历史无法提供有效上下文
- **模型**：Qwen2.5:3b（本地，JSON mode 强制合法输出，`temperature=0`）
- **置信度 < 0.5**：降级到 qa\_block（宁可多走 QA 也不冒险路由错误）
- **service 子类型**：意图识别只判断是否为 service，子类型（`ticket_type`）由 service 子图内部进一步分类
  | ticket\_type      | 触发场景             |
  | ----------------- | ---------------- |
  | `refund`          | 退货               |
  | `change`          | 换货               |
  | `quality`         | 商品破损、质量问题        |
  | `complain`        | 投诉               |
  | `equity`          | 权益申请（积分兑换、会员升级等） |
- **输出**：`intent`（主意图）+ `secondary_intents`（次意图列表）+ `confidence` + `reason`

**多意图处理**：

用户在单条消息中可能同时表达多个需求（如"先退货，然后推荐新品，再查积分政策"）。Prompt 约束 `secondary_intents` 最多2个（加上主意图共3个），超出部分丢弃，引导用户分次表达。

Router 将主意图立即路由到对应子图，次意图写入 `intent_queue`，服务依次串行执行：

```json
{
  "intent": "service",
  "secondary_intents": ["recommend", "qa"],
  "confidence": 0.92,
  "reason": "用户先要退货，随后表达购买意图，最后询问积分政策"
}
```

中途意图切换（子图内检测到其他意图）同样写入 `intent_queue`，两类场景统一由 post\_process 的队列消费逻辑处理。

### 4.2 服务边界

三个子图均通过 `interrupt()` 实现多轮，通过**不再调用 interrupt、直接退出子图**触发 post\_process。

| 子图        | 进行中信号                     | 结束信号                                |
| --------- | ------------------------- | ----------------------------------- |
| service   | interrupt 暂停（等待信息 / 等待确认） | 工单创建成功 或 用户取消，不再 interrupt          |
| recommend | interrupt 暂停（等待追问）        | 推荐交付完成，用户无进一步追问，不再 interrupt        |
| qa        | interrupt 暂停（等待追问）        | 轮次超阈值且不连贯，或达到强制上限 15 轮，不再 interrupt |

service 和 recommend 有**天然终止点**（业务目标达成），子图内部判断是否继续 interrupt。QA 无天然终止点，依赖轮次计数 + 连贯性检测决定是否退出。

***

## 5. 记忆系统

### 5.1 分层设计

| 层       | 存储                 | Key 范围     | TTL     | 管理方              |
| ------- | ------------------ | ---------- | ------- | ---------------- |
| 工作记忆    | Redis Checkpointer | thread\_id | 7天      | LangGraph 自动     |
| 长期记忆    | Redis asyncio      | user\_id   | 90天     | post\_process 节点 |
| SCRM 缓存 | Redis asyncio      | user\_id   | 5\~30分钟 | Skills 层         |

**工作记忆范围**：仅当前服务的 messages（一次服务 = 一个 thread\_id），服务结束后该 thread 废弃，新服务用新 thread\_id。

### 5.2 长期记忆结构

**服务历史**（情绪历史合并存储，不单独建 key）

```
Key: mem:service:{user_id}   TTL: 90天
```

```json
{
  "sessions": [
    {
      "thread_id": "user123_a1f3",
      "intent": "service",
      "date": "2026-04-09",
      "result": "工单 T001 已创建",
      "emotion_score": 0.3,
      "member_tier": "gold",
      "topic_tags": ["退货", "物流延误"]
    }
  ],
  "summary": "用户近期主要反映物流问题，情绪偏负面，已两次补偿"
}
```

字段说明：

- `sessions`：保留最近 5 条详细记录，超出时旧记录压缩进 `summary`
- `emotion_score`：0=极负面，1=极正面，子图各轮对用户消息做情绪二分类，取累积均值
- `member_tier`：从当次 SCRM 画像快照，补偿判断直接用，避免 post\_process 再查 SCRM
- `topic_tags`：2\~3 个关键词，service 子图结构化提取，qa/recommend 由 LLM 生成，供离线洞察引擎聚类

**为什么情绪历史不单独存**：情绪分是服务的属性，合并后一次 Redis 读取获得全部上下文，避免数据分裂，查询减少一次 IO。

**SCRM 缓存**

```
scrm:profile:{user_id}    TTL: 30分钟   # 用户画像（等级、偏好、生命周期阶段）
scrm:behavior:{user_id}   TTL: 5分钟    # 行为轨迹（浏览、加购、购买）
```

**未完成任务（pending\_task）**

范围严格收窄为两类，不存模糊意图：

| 类型      | 触发时机            | 示例                                                       |
| ------- | --------------- | -------------------------------------------------------- |
| ticket  | service 子图创建工单后 | `{type:"ticket", ticket_id:"T001", status:"processing"}` |
| restock | 用户明确说"到货提醒我"    | `{type:"restock", product_id:"P123", desc:"白色卫衣M码"}`     |

由子图在 state 中写入 `pending_task`，post\_process 统一持久化，最多保留 3 条。

### 5.3 Summary 压缩触发

在 post\_process 内判断，无需独立定时任务。**触发条件是记忆体积阈值，而非固定服务次数**：

```
写入 session 后：
  条数判断：len(sessions) > 20
    → 取 sessions[:-5]（保留最近5条之外的部分）
    → 批量交给 Qwen 压缩 → 追加到现有 summary
    → sessions 只保留最近 5 条
```

**为什么不用"每N次触发"**：服务频率因用户差异很大，固定次数可能导致高频用户频繁触发 LLM 压缩，低频用户长期不压缩导致 summary 失效。阈值触发确保只在记忆真正"满"时才压缩，大多数服务结束时 post\_process 无需额外 LLM 调用。

### 5.4 post\_process 完整职责

1. 提取服务摘要（service/recommend 结构化生成；QA 取最后一条 AI 消息）
2. 写入长期记忆（追加 session，超阈值触发 Qwen summary 压缩）
3. 情绪补偿判断（`emotion_score < 0.3` 且 `member_tier in [gold, platinum]` → 触发补偿券）
4. 处理 intent\_queue（队列非空 → pop 队首 → 直接路由到下一子图，无需经过 router）

### 5.5 数据流向

```
用户消息（携带 thread_id，首次不传则 API 入口自动生成）
   │
   ├─ [工作记忆] Checkpointer 加载当前 thread 的 messages + state
   │
   ├─ Router：意图识别（仅当前消息，仅首轮运行）
   │
   ├─ 子图执行（interrupt 多轮，checkpointer 快照每次暂停状态）
   │   ├─ [SCRM 缓存] get_customer_profile / get_behavior_tracking（Skills）
   │   ├─ [长期记忆] get_service_history 注入 prompt（最近3条 session + summary）
   │   └─ 每轮采集 emotion_score（二分类累积）
   │
   └─ post_process（子图自然退出后触发）
       ├─ [长期记忆] 写服务记录，超阈值（20条）压缩 summary
       ├─ [补偿] emotion低 + VIP → 补偿券
       └─ intent_queue 非空 → 直接路由下一子图（不经 router）
```

***

## 6. 可靠性设计

### 6.1 自愈（三层容错）

**层1：LangGraph 节点级重试（RetryPolicy）**

```python
workflow.compile(
    checkpointer=checkpointer,
    retry_policy=RetryPolicy(
        max_attempts=3,
        initial_interval=1.0,
        backoff_factor=2.0,
        retry_on=(httpx.TimeoutException, redis.ConnectionError),
    )
)
```

任何节点内抛出可重试异常时，LangGraph 自动从该节点重新执行，checkpointer 保证状态不丢失。

**层2：外部调用级重试（tenacity）**

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
)
async def create_ticket(payload): ...
```

**层3：服务级降级策略**

| 故障           | 降级行为                                |
| ------------ | ----------------------------------- |
| Ollama 不可用   | `confidence=0` → 降级 qa\_block，对话不中断 |
| DeepSeek 超时  | 3次重试后兜底回复，保存服务记录                    |
| SCRM API 不可用 | 跳过画像注入，用长期记忆中 `member_tier` 快照继续    |
| Redis 不可用    | 降级 MemorySaver，长期记忆写入失败静默丢弃并告警      |

**核心原则**：外部服务故障不中断用户对话，只降低服务质量。

### 6.2 不可逆写入调用的正确性保证（三道防线）

工单创建是不可逆操作，三道防线按顺序执行：

**防线1：Human-in-the-loop（LangGraph interrupt）**

```python
from langgraph.types import interrupt

async def confirm_and_create_ticket(state: ServiceState):
    summary = f"订单号：{state['extracted_order_id']}\n问题：{state['extracted_issue']}"
    # interrupt 暂停图，将 summary 返回用户；用户回复后图从此处恢复
    user_confirmation = interrupt({
        "type": "confirmation",
        "message": f"我将为您创建以下工单，请确认：\n{summary}",
    })
    if user_confirmation.lower() not in ["确认", "是", "对", "yes"]:
        return {"final_reply": "已取消", "current_subgraph": None}
    ticket = await scrm_api.create_ticket(...)
    ...
```

客户端收到 `interrupt` 事件，展示确认内容，用户下一条消息恢复图执行，`thread_id` 不变。

**防线2：写前验证（必填字段 + 业务规则）**

信息不完整时子图继续收集，不进入创建节点：

```python
if not state.get("extracted_order_id") or not state.get("extracted_issue"):
    return {"final_reply": "请提供订单号和问题描述", "current_subgraph": "service_block"}
```

**防线3：幂等锁（防重复提交）**

```python
idempotency_key = f"{state['thread_id']}:{state['extracted_issue'][:20]}"
lock_key = f"ticket_lock:{idempotency_key}"
if await redis.get(lock_key):
    return {"final_reply": "工单已创建，请勿重复提交", "current_subgraph": None}
await redis.setex(lock_key, 300, "1")   # TTL 5分钟
ticket = await scrm_api.create_ticket(...)
```

防止网络重试或用户快速连发消息导致重复创建工单。

### 6.3 查询接口防雪崩（四层防护）

**层1：入口限流（slowapi）**

```python
@limiter.limit("20/minute")          # 单 IP
@limiter.limit("200/minute", key_func=lambda r: r.headers.get("X-User-Id", "anon"))
async def chat(request): ...
```

超限返回 `429`，客户端应实现指数退避重试。

**层2：LLM 并发控制（asyncio Semaphore）**

```python
_local_semaphore = asyncio.Semaphore(5)    # Ollama 本地资源有限
_remote_semaphore = asyncio.Semaphore(20)  # DeepSeek QPS 限制

async with sem:
    return await llm.ainvoke(messages)
```

超出并发时请求排队等待，不打穿下游。

**层3：QA 语义缓存（Redis Stack SemanticCache）**

相似问题（"积分怎么用" ≈ "积分如何使用"）命中缓存后完全绕过 LLM 调用：

```python
set_llm_cache(RedisSemanticCache(
    redis_url=settings.redis_url,
    embedding=OllamaEmbeddings(model="nomic-embed-text"),
    score_threshold=0.95,
))
```

**仅对 QA 子图有意义**，service/recommend 响应依赖实时状态不可缓存。

**层4：下游熔断（circuitbreaker）**

```python
@circuit(failure_threshold=5, recovery_timeout=30, expected_exception=httpx.HTTPError)
async def call_scrm_api(endpoint, payload): ...
```

SCRM API 5次连续失败后熔断30秒，期间所有请求直接走降级路径，避免超时堆积。

**防护全景**

```
请求
 ├─ 限流（20/分/IP）────────────────── 超限 429
 ├─ LLM Semaphore（Ollama≤5，DS≤20）── 排队等待
 ├─ QA 语义缓存命中 ─────────────────── 直接返回，跳过 LLM
 ├─ 外部调用 tenacity 重试（3次退避）── 瞬时故障容错
 ├─ 外部调用 circuit breaker（5次熔断）─ 持续故障隔离
 └─ 节点 RetryPolicy ─────────────────── 兜底返回
```

***

## 7. Token 成本控制

### 7.1 监控方式

通过 LangChain Callback 在每次 LLM 调用后采集实际 token 用量，累计到当前服务的 state 中：

```python
class TokenUsageCallback(BaseCallbackHandler):
    def on_llm_end(self, response, **kwargs):
        usage = response.llm_output.get("token_usage", {})
        # 写入 state["token_usage_total"]（累加）
```

监控两个维度：

- **单次调用**：单个 LLM 请求的 input + output tokens
- **服务累计**：当前 thread 内所有 LLM 调用的 token 总量

### 7.2 阈值与降级层级

```
服务累计 token 用量
   │
   ├─ < 4000（正常）────────────────────── 继续，不干预
   │
   ├─ 4000~6000（警告）─────────────────── 降级记忆注入
   │      长期记忆注入从"最近3条+summary"
   │      压缩为"仅 summary"
   │
   ├─ 6000~8000（软限制）───────────────── 压缩当前 messages
   │      对 messages 做滑动窗口截断
   │      只保留系统 prompt + 最近 3 轮对话
   │
   └─ > 8000（硬限制/熔断）─────────────── 强制结束当前服务
          向用户返回"本次对话已超出处理上限，已为您保存进度"
          触发 post_process 写入记忆后退出
          同一 thread 不再接受新消息（checkpointer 标记为 closed）
```

### 7.3 各层降级行为

**警告层（4000\~6000）**：仅收窄记忆注入，对用户无感知，服务继续。

**软限制层（6000\~8000）**：消息截断，用户可能注意到 agent 对早期对话细节不再引用，但服务本身不中断。

**熔断层（>8000）**：

```python
if state.get("token_usage_total", 0) > 8000:
    return {
        "final_reply": "本次对话内容较多，已为您保存当前进度。如需继续请重新发起。",
        "force_end": True   # post_process 读取此标记，写入 pending_task 或 summary
    }
```

### 7.4 ReAct / P\&E 步数兜底

防止 LLM 循环导致 token 失控的硬上限（独立于累计阈值）：

- QA / Recommend：`create_react_agent(max_iterations=5)`，超出强制停止 ReAct 循环
- Service：P\&E 计划步数 prompt 约束不超过 5 步，Reflect 最多触发 2 次

***

## 8. API 文档

### POST /api/v1/chat

**请求**

```json
{
  "user_id": "user123",
  "message": "我要退货",
  "thread_id": "user123_a1f3",
  "channel": "wechat"
}
```

- `thread_id`：首次不传则后端自动生成；服务结束后响应含 `next_thread_id`，客户端下次携带新值

**响应（服务进行中）**

```json
{
  "reply": "好的，请提供您的订单号",
  "thread_id": "user123_a1f3",
  "metadata": {
    "intent": "service",
    "confidence_score": 0.95,
    "reason": "用户明确表达退货需求",
    "next_thread_id": null
  }
}
```

**响应（服务结束）**

```json
{
  "reply": "工单 T001 已创建，我们将在24小时内处理",
  "thread_id": "user123_a1f3",
  "metadata": {
    "intent": "service",
    "confidence_score": 0.95,
    "reason": "用户明确表达退货需求",
    "next_thread_id": "user123_b4e2"
  }
}
```

**响应（interrupt 等待确认）**

```json
{
  "reply": "我将为您创建以下工单，请确认：\n订单号：123456\n问题：商品破损",
  "thread_id": "user123_a1f3",
  "metadata": {
    "interrupt": true,
    "next_thread_id": null
  }
}
```

用户下一条消息（"确认" / "取消"）将恢复图执行，使用相同 `thread_id`。

### GET /health

```json
{"status": "ok"}
```

***

## 8. 配置说明

### 环境变量（.env）

```bash
# Redis
REDIS_URL=redis://redis:6379/0

# Ollama（路由模型，本地部署）
OLLAMA_BASE_URL=http://192.168.1.100:11434
OLLAMA_TIMEOUT=60.0

# DeepSeek（业务模型）
DEEPSEEK_API_KEY=sk-...

# 应用
DEBUG=false
LOG_LEVEL=INFO
```

### 端口映射

| 服务            | 容器端口 | 默认宿主机端口 | 环境变量                 |
| ------------- | ---- | ------- | -------------------- |
| backend       | 8000 | 8000    | `API_PORT`           |
| frontend      | 3000 | 3000    | `FRONTEND_PORT`      |
| redis         | 6379 | 6379    | `REDIS_PORT`         |
| redis insight | 8001 | 8001    | `REDIS_INSIGHT_PORT` |

***

## 9. 部署指南

### 本地开发

```bash
# 启动 Redis（需要 Redis Stack 支持 Search 模块）
docker run -d -p 6379:6379 redis/redis-stack:latest

# 后端
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# 前端
cd frontend && npm install && node server.js
```

### Docker 部署

```bash
# 生产
docker-compose up -d

# 开发（热重载）
docker-compose --profile dev up backend-dev

# 查看日志
docker-compose logs -f backend
```

**注意**：必须使用 `redis/redis-stack` 镜像，普通 `redis` 镜像缺少 Search 模块，`AsyncRedisSaver.setup()` 会失败。

***

## 11. 待实现功能

### 11.1 路由层

- [ ] 多意图识别 prompt（`secondary_intents` 最多2个）
- [ ] `intent_queue` 写入逻辑
- [ ] 子图内中途意图切换检测（追加到 `intent_queue`）

### 11.2 Service 子图（Plan-and-Execute-Reflect）

- [ ] interrupt 多轮驱动（信息收集 / 等待确认均通过 interrupt 暂停）
- [ ] 信息收集节点（订单号、问题描述提取）
- [ ] 动态计划生成（依 ticket\_type 生成步骤序列，不超过5步）
- [ ] 写前验证（必填字段检查）
- [ ] interrupt 确认节点（Human-in-the-loop）
- [ ] 工单创建（SCRM API + tenacity 重试 + 幂等锁）
- [ ] Reflect 节点（步骤失败 / 用户意图纠正时修订计划，最多触发2次）
- [ ] 部门匹配与工单完成后通知
- [ ] 情绪分采集（每轮二分类）
- [ ] pending\_task 写入（ticket 类型）

### 11.3 QA 子图（ReAct）

- [ ] interrupt 多轮驱动（每次 ReAct 完成后 interrupt，等待追问）
- [ ] `create_react_agent(max_iterations=5)`
- [ ] 工具：知识库检索（pgvector）
- [ ] 工具：用户画像获取（SCRM Skills）
- [ ] 工具：服务历史读取（长期记忆 Skills）
- [ ] 连贯性检测（Qwen，`qa_turn_count >= 5` 触发）
- [ ] 情绪分采集（每轮二分类累积）

### 11.4 Recommend 子图（ReAct）

- [ ] interrupt 多轮驱动
- [ ] `create_react_agent(max_iterations=5)`
- [ ] 工具：商品检索（库存 + 偏好过滤）
- [ ] 工具：行为轨迹读取（SCRM Skills）
- [ ] 优惠策略生成
- [ ] pending\_task 写入（restock 类型）

### 11.5 post\_process 节点

- [ ] 服务记录写入长期记忆
- [ ] Summary 压缩（超20条阈值触发 Qwen）
- [ ] 情绪补偿判断（`emotion_score < 0.3` + VIP → 补偿券）
- [ ] `intent_queue` pop 处理（直接路由到下一子图，不经 router）

### 11.6 Token 监控

- [ ] `TokenUsageCallback` 实现（累计写入 state）
- [ ] 警告层（4000\~6000）：记忆注入降级为仅 summary
- [ ] 软限制层（6000\~8000）：messages 滑动窗口截断
- [ ] 熔断层（>8000）：强制结束服务，写入记忆后退出

### 11.7 可靠性

- [ ] LangGraph RetryPolicy 配置
- [ ] SCRM API circuitbreaker
- [ ] LLM Semaphore 并发限制
- [ ] slowapi 入口限流
- [ ] QA 语义缓存（RedisSemanticCache）

### 11.8 Skills / 外部服务

- [ ] SCRM API 对接（`scrm_api.py`）
- [ ] SCRM Skills（`get_customer_profile`、`get_behavior_tracking`）
- [ ] 长期记忆 Skills（`get_service_history`）
- [ ] 知识库（pgvector，FAQ + 会员政策）

### 11.9 主动触达引擎（第二阶段）

- [ ] 行为事件触发（加购未支付30分钟、浏览未购买次日）
- [ ] 定时任务（Celery Beat / APScheduler + Redis 队列）
- [ ] 生命周期自动化（新客欢迎、沉默召回30天、流失挽回90天）
- [ ] 跨渠道推送（企微 / 短信 / 公众号）

### 11.10 洞察引擎（第二阶段）

- [ ] 用户反馈 topic\_tags 批量聚类分析
- [ ] 产品缺陷 / 需求报告生成（Plan-and-Execute-Reflect，离线任务）
- [ ] 经营建议输出

***

**最后更新**：2026-04-09extracted\_order\_id

```python
```
