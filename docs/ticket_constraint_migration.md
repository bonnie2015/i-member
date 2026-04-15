# Ticket 约束迁移清单

本文件记录 ticket 重构前后的核心约束归属，作为 skill / planner / executor / code 改动时的对照基线。

## 当前处理原则

- `skill`：保留领域规则、工具使用时机、查询/写操作优先级、隐私与合规要求。
- `planner prompt`：保留步骤粒度、计划结构、重规划边界、宏观步骤约束，以及 `available_tools` 的输出规则。
- `executor prompt`：保留执行边界、运行时动作决策、fallback 追问规则、interrupt 与最终 JSON 输出规则。
- `code`：保留状态结构、schema 校验、交互协议、槽位合并和失败收口逻辑。

## 约束对照

| 约束 | 当前归属 | 说明 |
| --- | --- | --- |
| 字段名、请求参数、返回结构以 tool/API 为唯一来源 | skill | 保留在业务 skill 中，作为领域层统一约束。 |
| `user_id` 不作为业务工具显式入参 | skill | 保留在业务 skill 中。 |
| 订单类写操作前必须具备 `source_channel` | skill | 保留在订单相关业务 skill 中。 |
| 写操作前必须确认关键对象、数量、问题描述/原因 | skill | 保留在业务 skill 中。 |
| 多商品订单必须先收敛到具体商品 | skill | 保留在订单相关业务 skill 中。 |
| 质量问题缺图片时不进入写操作 | skill | 保留在 `refund-ticket` / `unsatisfy-ticket` 中。 |
| 查询优先于不必要追问 | skill | 保留在业务 skill 中。 |
| 工单进度查询优先 `get_ticket` / `get_tickets` | skill | 保留在业务 skill 中。 |
| 隐私脱敏、越权拒绝、失败不编造 | skill | 保留在业务 skill 中。 |
| `steps` 上限、宏观步骤粒度、`available_tools` 输出边界 | planner prompt | 由 planner 只输出路线图，不再直接决定 ask_user/tool/interacting。 |
| `target_slots` 的定义与命名边界 | planner prompt + code | prompt 负责原则，code 负责最终校验和合并。 |
| 不提前使用未来步骤数据 | planner prompt | 后续保留在 planner prompt 中。 |
| 运行时是先查、先问还是先交互 | executor prompt | 由 executor 根据当前 step.goal、completion_signal、target_slots、available_tools 以及当前已知信息自行判断；planner 负责把 executor 无法隐式推断的业务限制写进步骤设计。 |
| 动态业务工具加载白名单 | code | executor 只加载 planner 当前步骤建议的业务工具，再固定附加 interrupt。 |
| `tool` 失败后的 fallback 追问必须有实际消费价值 | executor prompt | 后续收口到 executor prompt。 |
| 不重复询问已在 `slots` 或最近会话中明确给过的信息 | executor prompt + code | prompt 负责约束，code 负责入槽和复用。 |
| interaction schema 与模板 | code + executor prompt | 以 `interaction.py` 为唯一协议来源，由 executor 在运行时自主选择和使用。 |
| step schema 校验、重规划阈值、失败收口 | code | 保留在 `planner.py` / `reflect.py` / `finalizer.py`。 |

## 本轮 skill 重构结果

- 已从业务 skill 中去掉明显面向当前 planner 的格式/JSON/step 输出描述。
- 保留了所有直接影响业务判断和工具使用的规则。
- 未迁移到 prompt 的约束没有删除；若后续要迁移，必须先在本表中更新归属，再改动运行时文件。
