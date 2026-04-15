from typing import Optional

from pydantic import BaseModel, Field

from app.models.interaction import InteractionPayload


class ChatRequest(BaseModel):
    """聊天请求模型"""

    message: str = Field(..., description="用户消息内容")
    thread_id: Optional[str] = Field(None, description="会话线程ID，不传则自动生成")
    channel: str = Field("api", description="渠道来源: wechat/app/web/jd/tmall/douyin/api")

class ChatResponse(BaseModel):
    """普通聊天响应模型"""

    thread_id: str = Field(..., description="会话线程ID")
    reply: str = Field(..., description="主回复文本")
    interaction: Optional[InteractionPayload] = Field(None, description="交互信息")
