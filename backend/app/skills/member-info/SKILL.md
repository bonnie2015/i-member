---
name: member-info
description: 提供查询和操作会员信息的工具集合，包括标签、等级、基本资料、行为记录及会员升级功能。如需获取用户最新的会员信息或执行会员升级操作，请使用本 skill 提供的工具接口。包含接口：查询用户标签、等级、基本资料、行为记录、会员升级。
---

# 会员信息

## 使用声明

本 skill 用于查询和操作会员信息，包括标签、等级、基本资料、行为记录及会员升级。
调用前须持有合法的 `user_id`，所有接口均为只读查询，**upgrade_membership 除外**（写操作，需在执行前向用户确认）。

---

## 使用原则

1. **最小调用**：仅在当前任务确实需要某字段时才调用对应工具，不要预防性地批量拉取用户信息。
2. **先查后写**：执行 `upgrade_membership` 前，须先通过 `get_user_level` 确认当前等级，避免重复升级。
3. **信息保护**：返回结果中的手机号、邮箱等敏感字段不得完整透传给用户，仅用于内部逻辑判断。
4. **失败处理**：工具返回 `"success": false` 或抛出异常时，不得猜测结果，需告知用户操作失败并建议重试。

---

## 使用方法

1. 根据当前任务判断需要哪些会员信息。
2. 选择对应工具，构造 tool call（见下方格式）。
3. 等待工具返回结果后，提取所需字段继续处理。
4. 如需升级会员，先查询当前等级 → 向用户确认 → 再调用升级工具。

---

## 可供选择的工具

| 工具名 | 功能 |
|---|---|
| `get_user_tags` | 获取用户标签列表及标签详情 |
| `get_user_level` | 获取用户当前会员等级、积分及距下一等级所需积分 |
| `get_customer_profile` | 获取用户基本信息（聚合：等级、积分、标签、消费概况） |
| `get_user_behavior` | 获取用户行为记录（浏览、购买、退货等事件流） |
| `upgrade_membership` | 为用户执行会员升级（写操作） |

---

## 工具调用方法

### get_user_tags

```json
{
  "type": "tool_use",
  "name": "get_user_tags",
  "input": {
    "user_id": "string  // 必填，用户唯一标识"
  }
}
```

**返回示例**
```json
{
  "user_id": "u_001",
  "tags": ["高价值", "复购客户", "活跃用户"],
  "tag_details": [
    { "tag_id": "T001", "name": "高价值", "category": "消费", "added_at": "2024-01-10" }
  ]
}
```

---

### get_user_level

```json
{
  "type": "tool_use",
  "name": "get_user_level",
  "input": {
    "user_id": "string  // 必填"
  }
}
```

**返回示例**
```json
{
  "user_id": "u_001",
  "level": "黄金会员",
  "level_code": 3,
  "points": 8500,
  "points_to_next": 1500,
  "next_level": "铂金会员",
  "valid_until": "2025-12-31"
}
```

---

### get_customer_profile

```json
{
  "type": "tool_use",
  "name": "get_customer_profile",
  "input": {
    "user_id": "string  // 必填"
  }
}
```

**返回示例**
```json
{
  "user_id": "u_001",
  "name": "张三",
  "phone": "138****8888",
  "email": "z***@example.com",
  "member_level": "黄金会员",
  "level_code": 3,
  "points": 8500,
  "total_orders": 42,
  "total_spend": 15800.00,
  "tags": ["高价值", "复购客户"],
  "register_date": "2022-06-01",
  "last_active": "2024-11-20"
}
```

---

### get_user_behavior

```json
{
  "type": "tool_use",
  "name": "get_user_behavior",
  "input": {
    "user_id": "string",
    "event_type": "string | null  // 可选，枚举：browse / purchase / return，null 表示全部",
    "limit": "integer  // 可选，默认 20，最大 100"
  }
}
```

**返回示例**
```json
{
  "user_id": "u_001",
  "total": 3,
  "events": [
    {
      "event_id": "E1001",
      "type": "purchase",
      "description": "购买商品 SKU-888",
      "amount": 299.00,
      "timestamp": "2024-11-18 14:32:00"
    }
  ]
}
```

---

### upgrade_membership

> ⚠️ 写操作，调用前须向用户明确确认。

```json
{
  "type": "tool_use",
  "name": "upgrade_membership",
  "input": {
    "user_id": "string  // 必填",
    "target_level": "string | null  // 可选，指定目标等级；null 则按积分规则自动升级"
  }
}
```

**返回示例**
```json
{
  "user_id": "u_001",
  "success": true,
  "old_level": "黄金会员",
  "new_level": "铂金会员",
  "message": "升级成功！欢迎成为铂金会员",
  "benefits": ["专属客服通道", "生日双倍积分", "每季度免费礼品", "优先发货"]
}
```
