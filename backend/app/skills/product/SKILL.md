---
name: product
description: 提供查询商品信息和促销活动的工具集合，所有接口均为只读查询。商品查询支持关键词和分类过滤，促销查询支持按 SKU 和渠道过滤。包含接口：查询商品详情、查询当前有效促销活动。
---
# 商品

## 使用声明

本 skill 用于查询商品详情和当前有效促销活动，所有接口均为**只读查询**。

---

## 使用原则

1. **按需查询**：用户询问促销时不必先查商品详情；用户询问商品时不必拉取促销列表，根据实际需要选择工具。
2. **关键词精准**：调用 `query_product` 时，关键词尽量使用用户原话，避免过度泛化导致结果噪声过多。
3. **失败处理**：商品不存在或无匹配促销时如实告知用户，不得编造信息。

---

## 使用方法

1. 根据用户意图判断需要商品信息还是促销信息。
2. 构造 tool call，等待结果后提取所需字段继续处理。
3. 若需要同时展示商品和适用促销，先调 `query_product` 获得 `sku_id`，再以此调 `query_promotion`。

---

## 可供选择的工具

| 工具名 | 功能 |
|---|---|
| `query_product` | 按商品名称关键词或 SKU ID 查询商品信息 |
| `query_promotion` | 查询当前有效促销活动（全场或指定商品） |

---

## 工具调用方法

### query_product

```json
{
  "type": "tool_use",
  "name": "query_product",
  "input": {
    "keyword": "string  // 必填，商品名称关键词或 SKU ID",
    "category": "string | null  // 可选，商品分类，如 '上衣' / '裤子' / '鞋履'，null 表示不限",
    "limit": "integer  // 可选，返回条数，默认 5，最大 20"
  }
}
```

**返回示例**
```json
{
  "total": 2,
  "products": [
    {
      "sku_id": "SKU-888",
      "name": "深蓝色卫衣",
      "category": "上衣",
      "price": 299.00,
      "stock": 152,
      "status": "on_sale"
    }
  ]
}
```

---

### query_promotion

```json
{
  "type": "tool_use",
  "name": "query_promotion",
  "input": {
    "sku_id": "string | null  // 可选，指定商品 SKU；null 表示查询全场促销",
    "channel": "string | null  // 可选，渠道过滤，枚举：app / wechat / web；null 表示全渠道"
  }
}
```

**返回示例**
```json
{
  "promotions": [
    {
      "promo_id": "P2024001",
      "title": "双十一全场8折",
      "type": "discount",
      "discount_rate": 0.8,
      "applicable_skus": [],
      "valid_from": "2024-11-11 00:00:00",
      "valid_until": "2024-11-11 23:59:59",
      "channels": ["app", "wechat", "web"]
    }
  ]
}
```
