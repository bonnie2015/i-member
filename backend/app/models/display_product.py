from __future__ import annotations

from pydantic import BaseModel, Field


class DisplayProductCard(BaseModel):
    """前端统一展示用的商品卡片模型。"""

    product_id: int = Field(description="商品 ID。")
    name: str = Field(description="商品名称。")
    price: float | int | None = Field(default=None, description="商品价格。")
    image: str = Field(default="", description="商品主图 URL。")
    official_url: str = Field(default="", description="官网详情页 URL。")
    color_id: int | None = Field(default=None, description="颜色 ID。")
    color_name: str = Field(default="", description="颜色名称。")
    category: str = Field(default="", description="分类名称。")
    gender: str = Field(default="", description="性别标签。")
    reason: str = Field(default="", description="推荐或展示理由。")
    in_stock: bool | None = Field(default=None, description="是否有货。")
    stock: int | None = Field(default=None, description="库存数。")
