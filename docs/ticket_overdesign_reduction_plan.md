# Ticket 链路减法方案

本文档用于收敛当前 ticket 链路中的过度设计问题，目标是在**不丢失现有约束和功能**的前提下，减少重复判断、减少中间结构、减少 prompt 解释、减少中间层业务猜测，让系统更接近工程上可维护、可扩展、可调试的状态。

总原则：

- 始终以最少层级、最少噪音、最统一、可扩展性强的事实来源做工程开发和重构。
- 优先改源头，避免中间层猜业务。
- 所有测试容器内完成。
- 所有重构都必须保证约束和功能不丢。

## 总体目标

将当前链路收敛为更清晰的职责分工：

- `router`：只负责大类路由与连续性判断
- `scene_guard`：只负责 ticket 内部场景收敛与 skill 读取
- `planner`：只负责宏观路线图
- `executor`：只负责当前步骤执行
- `code/tool/schema`：保留唯一事实来源

## Step 1. 合并入口连续性判断到 `router`

### 目标

减少入口层的重复判断，让“是否继续上一服务”“是否 direct reply”“是否进入 ticket/qa/recommend”只在一层完成。

### 具体动作

1. 将当前入口连续性判断逻辑并入 `router`。
2. `router` 最终只输出：
   - `intent`
   - `reason`
   - `is_continuous`
   - `is_direct_reply`
3. 删除独立的入口连续性判断 prompt 和仅服务它的中间桥接逻辑。
4. 保持 `ticket / qa / recommend` 的分流行为不变。

### 验收标准

- 入口节点减少 1 层。
- 用户连续追问时，仍能正确延续上一服务。
- 非连续话题仍能正确重新路由。
- 不因合并导致 ticket/qa/recommend 的分流准确率明显下降。

## Step 2. 收薄 `scene_guard`

### 目标

让 `scene_guard` 只承担 ticket 子图内部的第一层场景收敛，不承担执行策略和规划职责。

### 具体动作

1. `scene_guard` 只保留：
   - scene 判断
   - 必要时通过 `interrupt` 发起封闭式追问
   - 读取唯一对应的业务 skill 正文
2. 不让 `scene_guard` 承担：
   - planner 级步骤设计
   - executor 级执行策略
   - 任何写操作前置判断的细执行逻辑
3. prompt 只保留：
   - scene 可选范围
   - 何时使用 `interrupt`
   - 何时使用 `read_file`
   - 最终 JSON 输出要求
4. 保证 scene 澄清过程中的对话都直接进入 `messages`，后续连续性判断只读这一份消息源。

### 验收标准

- `scene_guard.py` 明显变短，不再有多层控制流补丁。
- `scene_guard` 缺少场景线索时会优先追问，而不是直接错误落到某个 scene。
- scene 明确时会稳定输出 `ticket_scene` 与 `selected_skill_content`。
- 不再引入新的仅服务 `scene_guard` 的 state 字段。

## Step 3. planner 继续压成纯路线图

### 目标

让 planner 只做宏观计划，不再承担执行动作决策和中间兜底逻辑。

### 具体动作

1. `TicketPlanStep` 只保留：
   - `id`
   - `goal`
   - `completion_signal`
   - `target_slots`
   - `available_tools`
   - `is_success`
   - `result`
2. 不再回加：
   - `type`
   - `tool_name`
   - `reply`
   - `interaction_type`
   - `interaction_candidates`
3. planner prompt 只保留：
   - 宏观 plan 输出结构
   - 5 步内约束
   - skill 中业务限制如何体现在 `completion_signal`
   - 真实工具边界
4. 删除 planner prompt 中与当前层无关的实现解释和过多命名规则说明。

### 验收标准

- planner 输出保持 3 到 5 步。
- step 结构没有重新膨胀。
- planner 不再编造动作类型和交互字段。
- planner 产出的 `available_tools` 只来自真实工具集合。

### 当前进展

- 已完成：step 结构已稳定收成 `id / goal / completion_signal / target_slots / available_tools / is_success / result`。
- 已完成：删除了动作类型、交互字段和执行层字段。
- 本轮已完成：`plan.txt` 再压缩一轮，删除大段重复规划原则、实现解释和冗长命名说明，只保留当前层真正需要的规划约束。
- 当前残留：
  - `planner.py` 仍会把完整 prompt 打到日志，日志噪音偏大。
  - tool summary 仍是自然语言摘要，后续还可以继续压成更紧凑格式。

## Step 4. executor 只做当前步执行

### 目标

executor 不再猜业务规则，不再感知 skill，只围绕当前 step 执行。

### 具体动作

1. executor 输入只保留：
   - `goal`
   - `completion_signal`
   - `target_slots`
   - `available_tools`
   - `slots`
   - 最近消息
2. executor prompt 只保留：
   - 当前步内如何决定查 / 问 / 交互
   - 不问无法消费的信息
   - 不重复问已知信息
   - 最终 JSON 输出格式
3. 继续保留动态工具加载：
   - 只从 `available_tools` 里挂业务工具
   - 固定附加 `interrupt_tool`
4. 不给 executor 注入 skill 正文，不让它承担资格规则的隐式推断。

### 验收标准

- executor prompt 再缩短一轮。
- executor 不再持有 skill 内容。
- executor 仍能完成当前 step 的查 / 问 / 交互决策。
- 不新增任何业务 if/else 猜测代码。

### 当前进展

- 已完成：executor 不再注入 skill。
- 已完成：动态工具加载只消费 `available_tools + interrupt_tool`。
- 本轮已完成：`execute.txt` 再压缩一轮，删除上层架构说明和重复规则，只保留当前步执行规则、数据规则和最终 JSON 约束。
- 当前残留：
  - executor 仍会收到完整当前 step JSON 和最近消息，这一层输入还能继续评估是否再瘦。
  - 交互正向样本仍偏少，后续需要在稳定 mock 下继续补验证，但这属于验证问题，不再先加结构。

## Step 5. 收缩 state 字段

### 目标

state 只保留运行时真正需要的字段，避免同一事实被多个字段重复表达。

### 建议保留

- `ticket_scene`
- `current_goal`
- `selected_skill_content`
- `steps`
- `current_step_index`
- `slots`
- `expected_slots`
- `next_action`
- `final_status`
- `final_reason`
- `messages`

### 具体动作

1. 清理只服务某一层 prompt 的临时字段。
2. 清理历史兼容字段和重复摘要字段。
3. 保持对话连续性只依赖 `messages`，不再维护平行消息源。

### 验收标准

- state 字段总量不再增长。
- 不存在两个字段表达同一事实。
- 后续连续性判断只需读取 `messages` 和必要摘要字段。

## Step 6. 保持 code 为事实来源

### 目标

所有事实来源尽量回到 code/tool/schema，不让 prompt 和中间层重复维护真相。

### 具体动作

1. 工具真相保留在：
   - `scrm_tools.py`
   - `TOOL_SPECS`
   - 规范化输出逻辑
2. interaction 协议真相保留在：
   - `interaction.py`
3. 业务规则真相保留在：
   - 各业务 skill
4. planner 只消费：
   - scene
   - skill
   - 工具摘要
5. executor 只消费：
   - step
   - 真实 tool result
6. 不新增低价值契约表、桥接层、业务猜测正则和 case 补丁。

### 验收标准

- 工具定义只有一份源头。
- prompt 中不再堆大量“解释系统怎么实现”的内容。
- 中间层代码不再持续增长。

## Step 7. 继续压缩 prompt

### 目标

让每层 prompt 只描述当前层任务，不解释架构，不堆 case。

### 具体动作

1. `scene_guard` prompt 只描述：
   - scene 判断
   - interrupt 使用条件
   - skill 读取要求
   - 输出 JSON
2. `planner` prompt 只描述：
   - plan 结构
   - 宏观步骤规则
   - skill 限制如何下沉到 `completion_signal`
   - 真实工具边界
3. `executor` prompt 只描述：
   - 当前步如何执行
   - 不问无法消费的信息
   - 最终 JSON 输出
4. 删除：
   - workflow 自述
   - 与本层无关的实现细节
   - 过多重复约束

### 验收标准

- 三份 prompt 都明显更短。
- 没有明显语义重复和实现自述。
- 当前行为不回退。

## Step 8. 评估是否继续薄化 `reflect`

### 目标

在前面减法完成后，评估 `reflect` 是否仍然承担不可替代职责。

### 具体动作

1. 检查 `reflect` 当前真实职责：
   - 是否只是判断下一步 / finalize
   - 是否有不可替代的恢复逻辑
2. 若主要是简单推进判断，评估并入 executor/finalizer 的可行性。
3. 不在本轮直接删除，只做评估与回归验证。

### 验收标准

- 明确 `reflect` 是否仍需要单独保留。
- 若可以合并，形成下一阶段减法方案。

## 推荐执行顺序

1. 合并入口连续性判断到 `router`
2. 收薄 `scene_guard`
3. 继续压 `planner`
4. 继续压 `executor`
5. 收缩 state
6. 巩固 code 事实来源
7. prompt 再压缩
8. 评估 `reflect`

## 回归测试要求

所有测试仅在容器内完成，至少覆盖：

1. 质量问题创建
2. 模糊退货两轮
3. 工单进度查询
4. 权益未到账
5. `others` 收口

重点检查：

- 入口是否只判断一次大类
- scene 收敛是否更稳
- planner 是否更短
- executor 是否不再问无意义问题
- `slots` 是否只保留稳定字段
- 工具边界是否没有回退
