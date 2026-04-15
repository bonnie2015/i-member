# Ticket 全链路容器回归报告

更新时间：2026-04-18

## 说明

- 所有测试均在 Docker 容器内完成。
- 未修改或依赖本机真实运行环境。
- 主回归脚本：
  - [backend/app/scripts/ticket_container_regression.py](/Users/xyg/就业/AI/Agent/i-member/backend/app/scripts/ticket_container_regression.py)
- 仓库内独立 mock 服务：
  - [mock_scrm/main.py](/Users/xyg/就业/AI/Agent/i-member/mock_scrm/main.py)

## 本轮覆盖

- `router -> scene_guard -> planner -> executor -> reflect -> END`
- `scene_guard` 多轮澄清
- `reflect` 合并收口
- 标准 `scrm` 服务下的核心 ticket 场景
- 非 ticket 路由隔离
- 前端可见交互类型契约：
  - `select_order`
  - `select_product`
  - `select_ticket`

## 环境与数据源

- 仓库内标准 `scrm` 服务：
  - `http://scrm:3658`
- 提供稳定订单、订单详情、工单列表与工单详情
- 可触发：
  - `select_order`
  - `select_product`
  - `select_ticket`

## 当前回归结果

这是当前标准 `scrm` 服务上的完整矩阵结果。

| Mode | Case 数 | 原始通过数 | 原始通过率 |
|---|---:|---:|---:|
| `standard` | `12` | `12` | `100%` |
| **合计** | **12** | **12** | **100%** |

### 最终口径

| 指标 | 数值 |
|---|---:|
| 总 case 数 | `12` |
| 最终通过数 | `12` |
| 最终正确率 | `100%` |

## 关键验证结果

### 1. 入口减法已生效

- `router_guard` 已并回 `router`
- `router` 只做一次连续性 + 大类判断
- ticket / QA / recommend 分流正常

### 2. `scene_guard` 已承担 ticket 入口收敛

- 显式退货 -> `refund`
- 显式换货 -> `change`
- 投诉 -> `complain`
- 权益 -> `equity`
- 模糊工单查询 -> 连续澄清，必要时收口

### 3. planner 已收成宏观路线图

当前 step 只保留：

- `goal`
- `completion_signal`
- `target_slots`
- `available_tools`

已不再输出：

- `type`
- `tool_name`
- `interaction_type`
- `interaction_candidates`
- `reply`

### 4. executor 已能自主生成交互

本轮已验证正向交互：

- `select_order`
- `select_product`
- `select_ticket`

说明：
- 交互决策权已经在 executor
- planner 不再控制交互

### 5. reflect 合并收口成功

- `scene_guard / planner / executor` 在需要结束时不直接结束图
- 它们只表达 `next_action = end`
- 统一流到 `reflect`
- `reflect` 生成 `final_reply` 后再走图的 `END`

## 前端渲染契约验证

### 前端现有支持

[frontend/script.js](/Users/xyg/就业/AI/Agent/i-member/frontend/script.js) 当前已支持：

- `select_order`
- `select_product`
- `select_ticket`
- `confirm_ticket`

对应代码位置：

- `normalizeInteraction`
- `handleInteraction`
- `toSelectableOptions`

### 后端返回契约

本轮回归已验证：

- `interaction.interaction_type` 非空
- `interaction.items` 为非空数组
- 每个 item 都有：
  - `key`
  - `label`
  - `detail`

这与前端当前渲染契约一致，可以正确渲染选择卡片。

### 前端容器可用性

本轮还修正了前端与 mock 容器的健康检查方式，使容器状态与真实可用性一致：

- `frontend`：已改为适配 `node:18-alpine` 的健康检查方式
- `scrm`：已改为适配 Python 镜像的健康检查方式

当前容器状态可稳定达到：

- `frontend: healthy`
- `scrm: healthy`

## 当前版本结论

### 功能可用性

- **当前版本全链路可用**
- **可作为阶段性提交版本**

### 是否建议直接作为最终收口版

- **暂不建议直接定义为最终版**

原因不是链路不通，而是：

1. 当前首轮耗时仍偏高
2. 工单查询路径还能继续精简
3. mock 已稳定，但仍不等于最终业务真值数据

## 仍值得继续优化的点

1. 工单进度查询路径还可以继续减字段负担
   - 继续减少非首要字段上浮
2. `SCRM` 不通场景下还可以再少问一些低价值问题
3. 如果要做最终业务验收，需要更接近真实业务语义的数据集
