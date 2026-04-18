# SCRM 系统接口

## tool 映射

### 订单

| 工具名 | 功能 | api |
|---|---|---|
| `get_user_orders` | 获取用户历史订单列表（支持分页、状态和时间筛选） | GET /order |
| `get_order_detail` | 获取单笔订单完整详情（商品、金额、状态、收货信息） | GET /order/{order_id} |

### 商品

| 工具名 | 功能 | api |
|---|---|---|
| `search_product` | 按商品名称关键词或 sku、id 查询商品信息 | GET /product |
| `get_product_detail` | 查询商品详情 | GET /product/{product_id} |
| `get_product_stock` | 查询商品库存 | GET /product/stock/{product_id} |


### 物流

| 工具名 | 功能 | api |
|---|---|---|
| `get_logistic` | 获取订单物流轨迹与当前状态 | GET /logistic |

### 会员

| 工具名 | 功能 | api |
|---|---|---|
| `get_user_detail` | 获取用户详细信息，包含等级、积分、标签、消费概况） | GET /user |
| `get_user_tag` | 获取用户标签列表及标签详情 | GET /user/tag |
| `get_user_level` | 获取用户当前会员等级、积分及距下一等级所需积分 | GET /user/level |
| `get_user_score` | 查询用户积分余额与积分明细（支持分页） | GET /user/score |
| `get_user_behavior` | 获取用户行为记录（浏览、购买、退货等事件流） | GET /user/eventLog |
| `upgrade_membership` | 为用户执行会员升级（写操作） | POST /user/upgrade |
| `issue_compensation_coupon` | 发放服务补偿券（写操作） | POST /coupon/compensation |

### 工单

| 工具名 | 功能 | api |
|---|---|---|
| `create_ticket` | 创建客服工单（退换货 / 质量问题上报 / 投诉 / 权益申请） | POST /ticket |
| `get_ticket` | 查询工单当前状态及处理进展 | GET /ticket/{ticket_id} |
| `get_tickets` | 查询当前用户工单列表（支持分页和时间筛选） | GET /ticket |

## api 文档

## 统一约定

- 鉴权：`Authorization: Bearer <access_token>`
- 响应格式：
  - 成功：`{"code":0,"message":"ok","data":{...}}`
  - 失败：`{"code":<非0>,"message":"错误信息","data":null}`
- 时间字段统一 ISO8601（如 `2026-04-13T10:30:00+08:00`）
- 分页字段统一：
  - `page`：页码，从 1 开始
  - `page_size`：每页数量，建议默认 20，最大 100
  - 返回包含 `total`、`page`、`page_size`、`has_more`

## 订单

### 获取订单列表

- API：`GET /order`
- 对应工具：`get_user_orders`
- 用途：搜索/筛选用户订单（分页）

Query 参数：

- `page` int，可选，默认 1
- `page_size` int，可选，默认 20，最大 100
- `status` string，可选（`pending`/`paid`/`shipped`/`delivered`/`closed`）
- `keyword` string，可选（支持订单号、商品名模糊搜索）
- `start_time` string，可选（ISO8601）
- `end_time` string，可选（ISO8601）

响应 data：

- `total` int
- `page` int
- `page_size` int
- `has_more` bool
- `orders` array
  - `order_id` string
  - `status` string
  - `status_label` string
  - `amount` number
  - `items_summary` string
  - `created_at` string

### 获取订单详情

- API：`GET /order/{order_id}`
- 对应工具：`get_order_detail`

Path 参数：

- `order_id` string，必填

Query 参数：

- 无（用户身份从鉴权上下文获取）

响应 data：

- `order_id` string
- `user_id` string
- `status` string
- `status_label` string
- `amount` number
- `created_at` string
- `address` string
- `items` array
  - `sku_id` string
  - `name` string
  - `qty` int
  - `price` number

## 商品

### 搜索商品

- API：`GET /product`
- 对应工具：`search_product`
- 用途：按关键词/SKU/商品ID搜索（分页）

Query 参数：

- `keyword` string，可选（商品名模糊）
- `sku_id` string，可选
- `product_id` string，可选
- `status` string，可选（`on_sale`/`off_sale`）
- `page` int，可选，默认 1
- `page_size` int，可选，默认 20，最大 100

响应 data：

- `total` int
- `page` int
- `page_size` int
- `has_more` bool
- `products` array
  - `product_id` string
  - `sku_id` string
  - `name` string
  - `price` number
  - `status` string
  - `stock` int

### 商品详情

- API：`GET /product/{product_id}`
- 对应工具：`get_product_detail`

Path 参数：

- `product_id` string，必填

响应 data：

- `product_id` string
- `name` string
- `description` string
- `brand` string
- `category` string
- `price` number
- `images` string[]
- `specs` object

### 商品库存

- API：`GET /product/stock/{product_id}`
- 对应工具：`get_product_stock`

Path 参数：

- `product_id` string，必填

响应 data：

- `product_id` string
- `total_stock` int
- `available_stock` int
- `locked_stock` int
- `updated_at` string

## 物流

### 查询物流

- API：`GET /logistic`
- 对应工具：`get_logistic`

Query 参数：

- `order_id` string，必填

响应 data：

- `order_id` string
- `carrier` string
- `tracking_no` string
- `status` string
- `status_label` string
- `tracks` array
  - `time` string
  - `desc` string
  - `location` string

## 会员

### 用户详情

- API：`GET /user`
- 对应工具：`get_user_detail`

Query 参数：

- 无（用户身份从鉴权上下文获取）

响应 data：

- `user_id` string
- `name` string
- `member_level` string
- `score` int
- `total_orders` int
- `tags` string[]

### 用户标签

- API：`GET /user/tag`
- 对应工具：`get_user_tag`

Query 参数：

- 无（用户身份从鉴权上下文获取）

响应 data：

- `user_id` string
- `tags` string[]
- `tag_details` array

### 用户等级

- API：`GET /user/level`
- 对应工具：`get_user_level`

Query 参数：

- 无（用户身份从鉴权上下文获取）

响应 data：

- `user_id` string
- `level` string
- `level_code` int
- `score` int
- `score_to_next` int
- `next_level` string

### 查询积分（搜索类，分页）

- API：`GET /user/score`
- 对应工具：`get_user_score`

Query 参数：

- `page` int，可选，默认 1
- `page_size` int，可选，默认 20，最大 100
- `start_time` string，可选（ISO8601）
- `end_time` string，可选（ISO8601）

响应 data：

- `user_id` string
- `score_balance` int
- `total` int
- `page` int
- `page_size` int
- `has_more` bool
- `records` array
  - `record_id` string
  - `change` int（正数=增加，负数=扣减）
  - `type` string（`earn`/`deduct`/`expire`/`adjust`）
  - `reason` string
  - `time` string

### 用户行为记录（搜索类，分页）

- API：`GET /user/eventLog`
- 对应工具：`get_user_behavior`

Query 参数：

- `event_type` string，可选（`view`/`purchase`/`refund`/`complaint`）
- `page` int，可选，默认 1
- `page_size` int，可选，默认 20，最大 100
- `start_time` string，可选（ISO8601）
- `end_time` string，可选（ISO8601）

响应 data：

- `total` int
- `page` int
- `page_size` int
- `has_more` bool
- `events` array
  - `event_id` string
  - `type` string
  - `description` string
  - `time` string
  - `amount` number，可选

### 会员升级（写操作）

- API：`POST /user/upgrade`
- 对应工具：`upgrade_membership`

Body 参数：

- `target_level` string，必填
- `reason` string，可选

响应 data：

- `user_id` string
- `success` bool
- `old_level` string
- `new_level` string
- `next_level_condition` object
  - `next_level` string
  - `required_score` int
  - `current_score` int
  - `gap_score` int
- `message` string

### 发放补偿券（写操作）

- API：`POST /coupon/compensation`
- 对应工具：`issue_compensation_coupon`

Body 参数：

- `reason` string，必填（补偿原因）
- `scene` string，可选（如 `service_recovery`）
- `amount` number，可选（固定面额券可不传）
- `expire_days` int，可选（默认值由券模板决定）

响应 data：

- `issued` bool
- `user_id` string
- `coupon_id` string
- `coupon_code` string
- `value` number
- `description` string
- `expires_at` string

## 工单

### 创建工单（写操作）

- API：`POST /ticket`
- 对应工具：`create_ticket`

Body 参数：

- `ticket_type` string，必填（`refund`/`change`/`quality`/`complain`/`equity`）
- `biz_id` string，必填（对应 `ticket_type` 的业务唯一 ID）
- `title` string，必填
- `content` string，必填
- `description` string，可选（补充说明）
- `images` string[]，可选（问题图片或凭证图片 URL 列表）
- `order_id` string，可选（订单类工单建议与 `biz_id` 一致）
- `order_item_id` string，可选（针对订单中的具体商品）
- `sku_id` string，可选
- `quantity` int，可选
- `priority` string，可选（`low`/`normal`/`high`/`urgent`，默认 `normal`）
- `source_channel` string，订单类必填（`wechat`/`app`/`web`/`jd`/`tmall`/`douyin`/`api`/`offline`）
- `contact` object，可选
  - `name` string，可选
  - `mobile` string，可选
  - `email` string，可选
- `metadata` object，可选（扩展字段，如证据摘要、业务标签）
- `attachments` string[]，可选

类型唯一 ID 约定：

- `refund`/`change`/`quality`：`biz_id = order_id`
- `complain`：`biz_id = complaint_id`
- `equity`：`biz_id = equity_request_id`

响应 data：

- `ticket_id` string
- `user_id` string
- `ticket_type` string
- `biz_id` string
- `status` string
- `status_label` string
- `priority` string
- `source_channel` string
- `description` string，可选
- `images` string[]，可选
- `created_at` string
- `expected_finish_time` string

### 工单详情

- API：`GET /ticket/{ticket_id}`
- 对应工具：`get_ticket`

Path 参数：

- `ticket_id` string，必填

Query 参数：

- `ticket_type` string，必填（按类型查询）
- `biz_id` string，必填（对应 `ticket_type` 的业务唯一 ID）
- `source_channel` string，订单类必填（`wechat`/`app`/`web`/`jd`/`tmall`/`douyin`/`api`/`offline`）

响应 data：

- `ticket_id` string
- `user_id` string
- `ticket_type` string
- `biz_id` string
- `status` string
- `status_label` string
- `title` string
- `content` string
- `description` string，可选
- `images` string[]，可选
- `order_id` string，可选
- `order_item_id` string，可选
- `sku_id` string，可选
- `quantity` int，可选
- `priority` string
- `source_channel` string
- `latest_progress` string
- `expected_finish_time` string
- `metadata` object
- `created_at` string
- `updated_at` string
- `timeline` array
  - `time` string
  - `action` string
  - `operator` string

### 工单列表（搜索类，分页）

- API：`GET /ticket`
- 对应工具：`get_tickets`

Query 参数：

- `ticket_type` string，必填（按类型查询）
- `biz_id` string，必填（对应 `ticket_type` 的业务唯一 ID）
- `source_channel` string，订单类必填（`wechat`/`app`/`web`/`jd`/`tmall`/`douyin`/`api`/`offline`）
- `status` string，可选（`open`/`processing`/`closed`）
- `keyword` string，可选（标题/内容模糊搜索）
- `start_time` string，可选（ISO8601）
- `end_time` string，可选（ISO8601）
- `page` int，可选，默认 1
- `page_size` int，可选，默认 20，最大 100

响应 data：

- `total` int
- `page` int
- `page_size` int
- `has_more` bool
- `tickets` array
  - `ticket_id` string
  - `ticket_type` string
  - `biz_id` string
  - `status` string
  - `title` string
  - `created_at` string

## 通用错误码建议

- `40001` 参数错误
- `40004` 资源不存在
- `40101` 未鉴权或 token 无效
- `40301` 用户范围校验失败（越权）
- `42901` 请求过于频繁
- `50001` 系统内部错误
