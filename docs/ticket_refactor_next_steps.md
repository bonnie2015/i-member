# Ticket 下一阶段重构 TODO 与验收

始终以最少层级、最少噪音、最统一、可扩展性强的事实来源做工程开发和重构；优先改源头，避免中间层猜业务；所有测试容器内完成，所有重构都必须保证约束和功能不丢。

## 目标

在不丢失现有约束和功能的前提下，继续把 ticket 链路收敛为更稳、更轻、更工程化的设计：

- 前置轻量 `scene` 收敛
- planner 只做已知场景下的宏观规划
- executor 做细执行
- 全局 `slots` 只保留稳定字段
- 细节字段留在真实 `tool_result`
- code 继续作为事实来源，不让中间层猜业务

## Step 1. 前置 Scene 收敛层

### 当前状态

- 已完成
- 已在 ticket 子图入口新增 `scene_guard`
- 已切到本地 `ollama` 模型做 scene 识别与 skill 预选
- 已调整为由 `scene_guard` 自己通过 agent 判断：
  - 缺少 scene 线索时使用 `interrupt` 追问
  - 最多追问 3 次
  - 超过次数后统一交给 `finalizer` 收口

### 目标

在 planner 前新增一个轻量 `scene guard`，使用本地 `ollama` 模型完成：

- 识别 `ticket_scene`
- 给出识别原因
- 选择一个最相关业务 skill

### 约束

- 只做轻量分类，不做详细规划
- 不新增 `ticket_intent`
- 若不属于 ticket 范围，直接设：
  - `ticket_scene = "others"`
  - `current_goal = 无法处理原因`
  - 交给 `finalizer` 统一回复

### 验收

- ticket 子图入口不再直接进入 planner
- `scene guard` 使用本地 `ollama`
- `others` 场景不再进入 planner
- `ticket_scene`、`current_goal`、`selected_skill_content` 能正确写入 state
- `scene_guard` 在缺少业务场景线索时会自己追问，不把 query 类模糊输入错误塞进具体 scene

## Step 2. planner 去掉 skill loading

### 当前状态

- 已完成
- planner 不再自己做 skill loading / read_file
- planner 现在直接消费 `scene_guard` 预选好的 skill 内容
- planner 也已去掉对交互类型的规划约束，不再输出 `interaction_type` / `interaction_candidates`

### 目标

planner 不再自己做 skill 选择和 `read_file`，只消费：

- 当前对话上下文
- 已识别 `ticket_scene`
- 已预读取的业务 skill

### 验收

- `planner.py` 删除运行时 skill-loading 逻辑
- planner prompt 不再包含技能快照和读取 skill 的指令
- planner 上下文体积明显缩小
- 质量问题 / 工单查询首轮仍能正确生成宏观 plan
- planner 生成的 step 只包含：
  - `goal`
  - `completion_signal`
  - `target_slots`
- `available_tools`
  - `is_success`
  - `result`

## Step 3. 收缩全局稳定槽位

### 当前状态

- 部分完成
- planner / executor prompt 已明确：
  - 全局只沉淀稳定、跨步复用字段
  - 工单凭证图片统一使用 `evidence_images`
  - 商品展示图片留在 `tool_result`

### 目标

只保留稳定、跨步复用的信息进入全局 `slots`。

优先保留：

- `order_id`
- `order_item_id`
- `product_id`
- `sku_id`
- `source_channel`
- `problem_description`
- `evidence_images`
- `ticket_id`
- `ticket_status`
- `expected_finish_time`

### 验收

- planner 不再频繁产出展示型、容器型、摘要型槽位
- executor 只把稳定字段写入 `current_slots`
- 细节字段更多保留在 `result.tool_result`

## Step 4. `scrm_tools` 统一字段命名

### 当前状态

- 部分完成
- `scrm_tools.py` 已开始对关键 tool 输出做统一规范化，新增 canonical 字段：
  - `order_status`
  - `order_status_label`
  - `product_name`
  - `product_description`
  - `product_images`
  - `ticket_status`
  - `ticket_status_label`
  - `ticket_title`
  - `ticket_description`
  - `evidence_images`

### 目标

把冲突字段的统一命名下沉到 `scrm_tools`，避免模型自己映射：

- `name`
- `status`
- `description`
- `images`

### 原则

- 原始 tool 返回保留，作为底层事实
- adapter 输出统一字段供模型消费
- planner / executor / slots 优先消费 adapter 后字段

### 验收

- 不同 tool 的同语义字段能统一命名
- prompt 中与命名相关的大段解释可以删减
- 槽位漂移显著下降

## Step 5. 媒体字段拆分

### 目标

避免未来商品图片和凭证图片混淆。

统一拆成：

- `evidence_images`：用户上传凭证图，可进入全局 `slots`
- `product_images`：商品展示图，只留在 `tool_result` / `interaction detail`

### 验收

- 全局稳定槽位中不再出现裸 `images`
- 质量问题创建流程只使用 `evidence_images` 作为凭证依据
- 商品图片不被误当成工单凭证图片

## Step 6. 压缩 planner / executor prompt

### 目标

在 scene 收敛和 adapter 落地后，再做 prompt 减法。

### 验收

- planner prompt 只保留：
  - 输出结构
  - 宏观步骤规则
  - 稳定槽位原则
  - `others` 处理
- executor prompt 只保留：
  - 当前宏观步内如何决定查 / 问 / 交互
  - 不问无法消费的信息
  - 不重复问已知信息
  - 最终 JSON 输出结构
- planner prompt 不再注入交互前置条件、交互类型白名单或其他上层实现细节
- executor 自主根据当前任务与交互模板决定是否发起 interaction

## Step 7. 回归测试

### 必测场景

- 质量问题创建
- 模糊退货两轮
- 工单进度查询
- 权益未到账
- `others` 收口

### 验收

- `scene` 识别更稳
- plan 仍控制在 5 步内
- executor 不再问无意义问题
- 同线程不重复追问已给过的信息

### 当前已完成回归

- 所有本轮测试均在容器内部执行，未触碰本机真实环境。
- 已验证：
  - 质量问题首轮：`scene_guard -> planner -> executor` 链路正常
  - 工单进度查询首轮：`scene_guard -> planner -> executor` 链路正常
  - recommend 路由不受本轮 ticket 改造影响
- 最新 smoke test 已验证：
  - planner 输出中不再包含交互字段
  - executor 在 SCRM 不通时会优先围绕当前步骤关键槽位收敛，不再尝试输出 planner 级交互约束
- 新增完整容器回归矩阵：
  - SCRM 不通
  - SCRM 可通（`host.docker.internal:3658`）
- 产出文档：
  - [test_case.md](/Users/xyg/就业/AI/Agent/i-member/docs/test_case.md)
  - [efficient_monitor.md](/Users/xyg/就业/AI/Agent/i-member/docs/efficient_monitor.md)

## 当前已确认问题

- query 场景已在技能约束与 planner 规则收紧后，稳定收敛为 `ticket_id / ticket_status` 主路径；后续只需继续观察是否存在个别 prompt 回退。
- refund / change 场景已通过 skill 约束统一把原因类信息归并为 `problem_description`；后续只需继续观察是否存在个别命名漂移。
- `SCRM` 调用链已撤回所有自动归一化和 compose 级辅助改动；容器内能否打通完全取决于实际环境变量配置。

## Step 8. 模型 A/B

### 目标

在结构稳定后，再做模型对比，不把模型替换当成主修复方案。

### 做法

- 只替换 planner 模型
- 或只替换 scene 收敛层模型
- 保持架构、prompt、adapter 不变

### 验收

- 对比 scene 稳定性
- 对比槽位漂移率
- 对比首响耗时与全链路耗时

## Step 9. 基于真实数据沉淀项目量化结果

### 目标

在所有重构、回归和性能对比完成后，基于真实测试数据沉淀可用于项目复盘和简历描述的量化结论。

### 做法

- 汇总重构前后的真实指标：
  - 首轮响应耗时
  - 全链路完成耗时
  - 场景识别稳定性
  - 无效追问情况
  - 关键场景成功率
- 基于最终容器内测试报告形成前后对比。
- 输出一版可用于简历/项目介绍的表述。

### 验收

- 所有量化描述都来自最终测试报告，不凭估计填写。
- 至少形成：
  - 1 条简历版单行描述
  - 1 版项目版 2 到 3 行描述
- 指标口径前后一致，可追溯到真实测试数据。
