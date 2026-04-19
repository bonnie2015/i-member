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

- `router -> scene_guard -> react_agent -> END`
- `scene_guard` 多轮澄清与服务识别
- 单一 `ticket_react_agent` 承担追问、交互、tool use 与统一收口
- 标准 `scrm` 服务下的核心 ticket 场景
- 边界与非常规对话
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

这是当前 B 方案在标准 `scrm` 服务上的最新矩阵结果。

| Mode | Case 数 | 通过数 | 通过率 |
|---|---:|---:|---:|
| `standard` | `13` | `13` | `100%` |
| `edge` | `5` | `5` | `100%` |
| **合计** | **18** | **18** | **100%** |

### 当前效率口径

| 指标 | 数值 |
|---|---:|
| `standard` 首轮平均耗时 | `44.4s` |
| `standard` 单 case 平均完成耗时 | `57.4s` |
| `edge` 首轮平均耗时 | `27.2s` |

## 关键验证结果

### 1. 入口减法已生效

- `router_guard` 已并回 `router`
- `router` 只做一次连续性 + 大类判断
- ticket / QA / recommend 分流正常

### 2. `scene_guard` 已承担 ticket 入口收敛

- 显式退货 / 换货 / 投诉 / 权益可直接进入对应服务
- 模糊工单查询先澄清，再进入对应服务
- `scene` 仅保留在 `scene_guard` 内部，不再作为后续执行主状态外溢

### 3. 单一 `ticket_react_agent` 已承接核心业务执行

当前 ticket 主链路中的业务推进由单一 ReAct Agent 完成，负责：

- 追问
- 交互
- tool use
- 信息收集
- 查询 / 创建 / 收口

已不再使用：

- `planner`
- `executor`
- `reflect`

### 4. 正向交互已验证

本轮已验证：

- `select_order`
- `select_product`
- `select_ticket`

说明：
- 当前交互决策权已经在 `ticket_react_agent`
- 业务内容由 skill 和 tools 驱动

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
2. `ticket_id` 直查体验仍可继续优化
3. mock 已稳定，但仍不等于最终业务真值数据

## 仍值得继续优化的点

1. `ticket_id` 直查路径还可以继续减少无效澄清
2. 混合意图场景的主路由策略还可继续优化
3. 如果要做最终业务验收，需要更接近真实业务语义的数据集
