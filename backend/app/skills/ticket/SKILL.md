---
name: ticket
description: 提供创建和查询客服工单的工具集合，包括退换货、破损质量、投诉及权益申请等工单类型。`create_ticket` 为写操作，须在收集完必要信息并得到用户确认后方可调用，且同一问题只创建一张工单。`get_ticket` 为只读查询，须同时传入 `user_id` 和 `ticket_id` 以防越权访问他人工单。包含接口：创建工单、查询工单。
---
# 工单

## 使用声明

本 skill 用于创建和查询客服工单。
`create_ticket` 为**写操作**，须在收集完必要信息并得到用户确认后方可调用，且每次对话中同一问题**只创建一张工单**。
`get_ticket` 为只读查询。

---

## 使用原则

1. **信息完整再创建**：调用 `create_ticket` 前，必须已明确工单类型、问题描述，订单相关工单还需确认 `order_id`。
2. **禁止重复创建**：同一对话中若已成功创建工单，不得再次创建，应引导用户通过工单号跟进。
3. **用户确认**：创建工单前须向用户明确本次操作内容（类型、描述摘要），得到确认后再调用。
4. **查询鉴权**：`get_ticket` 须同时传入 `user_id` 和 `ticket_id`，防止越权查询他人工单。
5. **失败处理**：创建失败时告知用户，提示稍后重试或转人工客服，不得重复自动重试。

---

## 使用方法

**创建工单流程：**
1. 与用户确认工单类型及问题详情。
2. 若涉及订单，通过 `order` skill 获取 `order_id`。
3. 向用户确认即将创建的工单内容。
4. 调用 `create_ticket`，将返回的 `ticket_id` 告知用户。

**查询工单流程：**
1. 确认已知 `ticket_id`（用户提供或本次对话中已创建）。
2. 调用 `get_ticket`，将状态和进展告知用户。

---

## 可供选择的工具

| 工具名 | 功能 |
|---|---|
| `create_ticket` | 创建客服工单（退换货 / 破损质量 / 投诉 / 权益申请） |
| `get_ticket` | 查询工单当前状态及处理进展 |

---

## 工具调用方法

### create_ticket

> ⚠️ 写操作，调用前须向用户确认工单内容。

```json
{
  "type": "tool_use",
  "name": "create_ticket",
  "input": {
    "user_id": "string  // 必填",
    "ticket_type": "string  // 必填，枚举：return_exchange / damage_quality / complaint / rights_apply",
    "title": "string  // 必填，工单标题，简明描述问题，不超过 50 字",
    "description": "string  // 必填，问题详细描述",
    "order_id": "string | null  // 可选，关联订单号；无订单关联时传 null",
    "priority": "string  // 可选，枚举：low / medium / high，默认 medium"
  }
}
```

**ticket_type 说明**

| 枚举值 | 适用场景 |
|---|---|
| `return_exchange` | 用户申请退货或换货 |
| `damage_quality` | 商品破损、质量问题 |
| `complaint` | 用户投诉服务或商品 |
| `rights_apply` | 会员权益申请（如补发积分、补偿券） |

**返回示例**
```json
{
  "ticket_id": "TK-20241120-0042",
  "status": "open",
  "ticket_type": "return_exchange",
  "title": "申请退货 - 深蓝色卫衣尺码不合适",
  "department": "售后处理中心",
  "priority": "medium",
  "created_at": "2024-11-20 14:05:00",
  "estimated_response": "1个工作日内"
}
```

---

### get_ticket

```json
{
  "type": "tool_use",
  "name": "get_ticket",
  "input": {
    "user_id": "string  // 必填，用于校验工单归属",
    "ticket_id": "string  // 必填"
  }
}
```

**返回示例**
```json
{
  "ticket_id": "TK-20241120-0042",
  "status": "processing",
  "status_label": "处理中",
  "ticket_type": "return_exchange",
  "title": "申请退货 - 深蓝色卫衣尺码不合适",
  "department": "售后处理中心",
  "assignee": "客服专员 李华",
  "created_at": "2024-11-20 14:05:00",
  "updated_at": "2024-11-20 16:30:00",
  "progress_notes": [
    { "time": "2024-11-20 16:30:00", "note": "已联系用户确认退货地址，等待用户回寄" }
  ],
  "estimated_resolution": "2024-11-23"
}
```
