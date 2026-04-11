---
name: order
description: 提供查询用户订单列表和订单详情的工具集合。查询时须同时持有 `user_id` 和 `order_id` 以防越权访问。物流查询请使用 `logistics` skill，商品和促销查询请使用 `product` skill。包含接口：查询用户订单列表、查询单笔订单详情。
---
# 订单

## 使用声明

本 skill 用于查询用户订单列表和单笔订单详情，所有接口均为**只读查询**。
查询订单详情时须同时持有 `user_id` 和 `order_id`，以防越权访问他人订单。

---

## 使用原则

1. **双重校验**：查询订单详情时必须同时传入 `user_id` 和 `order_id`，不得仅凭 `order_id` 查询。
2. **列表优先**：若用户未提供订单号，先通过 `get_user_orders` 列出近期订单，引导用户确认目标订单，再调 `get_order_detail`。
3. **按需下钻**：`get_user_orders` 返回的基础状态若已满足需求，无需再调 `get_order_detail`。
4. **失败处理**：订单不存在时如实告知用户，不得编造状态。

---

## 使用方法

1. 判断用户是需要订单列表还是某笔订单的详情。
2. 若用户未提供订单号，先调 `get_user_orders` 让用户确认目标订单。
3. 构造 tool call，等待结果后提取所需字段继续处理。

---

## 可供选择的工具

| 工具名 | 功能 |
|---|---|
| `get_user_orders` | 获取用户历史订单列表（支持分页和状态过滤） |
| `get_order_detail` | 获取单笔订单完整详情（商品、金额、状态、收货信息） |

---

## 工具调用方法

### get_user_orders

```json
{
  "type": "tool_use",
  "name": "get_user_orders",
  "input": {
    "user_id": "string  // 必填",
    "status": "string | null  // 可选，枚举：pending / paid / shipped / completed / refunding / closed，null 表示全部",
    "page": "integer  // 可选，页码，默认 1",
    "page_size": "integer  // 可选，每页条数，默认 10，最大 50"
  }
}
```

**返回示例**
```json
{
  "user_id": "u_001",
  "total": 42,
  "page": 1,
  "orders": [
    {
      "order_id": "ORD-20241115-001",
      "status": "shipped",
      "status_label": "已发货",
      "amount": 299.00,
      "created_at": "2024-11-15 10:22:00",
      "items_summary": "深蓝色卫衣 × 1"
    }
  ]
}
```

---

### get_order_detail

```json
{
  "type": "tool_use",
  "name": "get_order_detail",
  "input": {
    "user_id": "string  // 必填，用于校验订单归属",
    "order_id": "string  // 必填"
  }
}
```

**返回示例**
```json
{
  "order_id": "ORD-20241115-001",
  "user_id": "u_001",
  "status": "shipped",
  "amount": 299.00,
  "paid_at": "2024-11-15 10:25:00",
  "items": [
    { "sku_id": "SKU-888", "name": "深蓝色卫衣", "qty": 1, "price": 299.00 }
  ],
  "shipping_address": "北京市朝阳区 *** 收: 张三",
  "logistics_no": "SF1234567890"
}
```
