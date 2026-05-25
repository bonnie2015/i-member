from typing import Optional

from pydantic import BaseModel, Field

from app.models.display_product import DisplayProductCard
from app.models.interaction import InteractionPayload


class ChatRequest(BaseModel):
    """聊天请求模型"""

    message: str = Field(..., description="用户消息内容")
    thread_id: Optional[str] = Field(None, description="会话线程ID，不传则自动生成")
    channel: str = Field(
        "api", description="渠道来源: wechat/app/web/jd/tmall/douyin/api"
    )
    idempotency_key: Optional[str] = Field(
        None, description="幂等键，同一 key 的重复请求直接返回缓存结果，5 分钟有效"
    )


class ChatResponse(BaseModel):
    """普通聊天响应模型"""

    thread_id: str = Field(..., description="会话线程ID")
    reply: str = Field(..., description="主回复文本")
    interaction: Optional[InteractionPayload] = Field(None, description="交互信息")
    products: list[DisplayProductCard] = Field(
        default_factory=list, description="推荐商品列表；仅推荐场景返回。"
    )


class ChatMessageRecord(BaseModel):
    role: str = Field(..., description="消息角色，仅返回 user / assistant。")
    content: str = Field(..., description="消息内容。")
    products: list[DisplayProductCard] = Field(
        default_factory=list, description="该条消息携带的商品卡片。"
    )
    interaction: Optional[InteractionPayload] = Field(
        None, description="该条消息携带的结构化交互信息。"
    )


class LatestThreadResponse(BaseModel):
    thread_id: Optional[str] = Field(
        None, description="当前用户最近一次聊天的线程 ID。"
    )
    messages: list[ChatMessageRecord] = Field(
        default_factory=list, description="最近线程的消息记录。"
    )
