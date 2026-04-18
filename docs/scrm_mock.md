# SCRM Mock 使用说明

更新时间：2026-04-18

## 目标

- 在仓库内保留一份可复用、可预测的 SCRM mock。
- 供 `backend` 容器回归测试使用。
- 供前端通过 `backend -> scrm` 链路稳定看到交互信息。
- 支持模拟：
  - 正常查询
  - 空结果
  - 接口错误
  - 事实不一致

## 服务位置

- 服务代码：
  - [mock_scrm/main.py](/Users/xyg/就业/AI/Agent/i-member/mock_scrm/main.py)
- Docker Compose 服务：
  - [docker-compose.yml](/Users/xyg/就业/AI/Agent/i-member/docker-compose.yml)
  - 服务名：`scrm`

## 目录位置

`mock_scrm` 代码目录现在是仓库根目录下的独立服务实现，与：

- [backend](/Users/xyg/就业/AI/Agent/i-member/backend)
- [frontend](/Users/xyg/就业/AI/Agent/i-member/frontend)

并列存在，不再挂在 `backend/app` 内部。

## 默认地址

容器网络内地址：

- `http://scrm:3658`

backend 会固定调用该地址对应的 `scrm` compose 服务。

## 接口范围

当前 mock_scrm 直接提供 backend tools 需要的标准接口：

- `GET /order`
- `GET /order/{order_id}`
- `GET /product`
- `GET /product/{product_id}`
- `GET /product/stock/{product_id}`
- `GET /logistic`
- `GET /user`
- `GET /user/tag`
- `GET /user/level`
- `GET /user/score`
- `GET /user/eventLog`
- `POST /user/upgrade`
- `POST /coupon/compensation`
- `GET /ticket`
- `GET /ticket/{ticket_id}`
- `POST /ticket`

特点：
- 订单列表返回 2 个订单，可触发 `select_order`
- 指定订单详情返回 2 个商品项，可触发 `select_product`
- 工单列表返回 2 个工单，可触发 `select_ticket`

## 已覆盖的典型交互

使用默认正常数据时，当前 mock 已能稳定触发：

- `select_order`
- `select_product`
- `select_ticket`

这三类交互已被容器回归用于验证：
- backend 返回的 `interaction` 结构完整
- 前端现有渲染逻辑能消费这些类型

## 前端联调用途

前端并不直接请求 `scrm`，而是走：

- `frontend -> backend /api/v1/chat -> backend tools -> scrm`

因此只要 backend 在 Docker 内运行，前端就能通过真实聊天链路看到 `scrm` 返回的交互信息。

### 推荐启动方式

如果要在本地通过前端直接联调 mock，建议使用：

```bash
docker compose up -d --build frontend backend scrm
```

说明：

- `frontend` 访问：`http://localhost:3000`
- `backend` 访问：`http://localhost:8000`
- 基础 [docker-compose.yml](/Users/xyg/就业/AI/Agent/i-member/docker-compose.yml) 默认就包含 `scrm`
- backend 在 Docker 内会固定通过 `http://scrm:3658` 调用该服务
- 当前前端与 `scrm` 容器的 healthcheck 已修正为与镜像内容匹配的检查方式，可稳定变为 `healthy`

## 备注

- 该 mock 的目标是：
  - 提供稳定、可重复的链路验证样本
  - 验证交互 payload 与前端契约
- 它不是业务真值，不应用于最终业务语义验收。
