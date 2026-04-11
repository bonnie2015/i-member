---
name: logistics
description: 提供查询订单物流轨迹的工具集合。查询时须同时持有 `user_id` 和 `order_id` 以防越权访问，`order_id` 可通过 `order` skill 获取。包含方法：查询订单物流轨迹。
---

# 物流

## 使用声明

本 skill 用于查询订单的物流/快递轨迹，接口为**只读查询**。
查询时须同时持有 `user_id` 和 `order_id`；若尚未获得 `order_id`，须先使用 `order` skill 查询确认。

---

## 使用原则

1. **双重校验**：必须同时传入 `user_id` 和 `order_id`，不得仅凭 `order_id` 查询。
2. **前置确认**：若用户未提供订单号，先通过 `order` skill 的 `get_user_orders` 引导用户确认目标订单，再查物流。
3. **失败处理**：物流信息暂不可查（如刚发货）或快递单号不存在时，如实告知用户，不得编造轨迹。

---

## 使用方法

1. 确认已知 `order_id`（用户提供，或通过 `order` skill 查询获得）。
2. 构造 tool call，等待结果后将物流状态和最新轨迹告知用户。

---

## 可供选择的工具

| 工具名 | 功能 |
|---|---|
| `get_logistics` | 查询订单物流/快递轨迹 |

---

## 工具调用方法

### get_logistics

```json
{
  "type": "tool_use",
  "name": "get_logistics",
  "input": {
    "order_id": "string  // 必填",
    "user_id": "string  // 必填，用于校验订单归属"
  }
}
```

**返回示例**
```json
{
  "order_id": "ORD-20241115-001",
  "logistics_company": "顺丰速运",
  "logistics_no": "SF1234567890",
  "status": "in_transit",
  "status_label": "运输中",
  "estimated_delivery": "2024-11-17",
  "tracks": [
    { "time": "2024-11-16 08:30:00", "location": "北京转运中心", "description": "已到达分拣中心" },
    { "time": "2024-11-15 18:00:00", "location": "上海发货仓", "description": "已揽件" }
  ]
}
```
