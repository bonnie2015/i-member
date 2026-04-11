from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求模型"""
    user_id: str = Field(..., description="用户唯一标识")
    message: str = Field(..., description="用户消息内容")
    thread_id: Optional[str] = Field(None, description="会话线程ID，不传则自动生成")
    channel: str = Field("api", description="渠道来源: wechat/app/web/jd/tmall/douyin/api")


class ChatResponse(BaseModel):
    """标准聊天响应模型"""
    reply: str = Field(..., description="Agent回复内容")
    thread_id: str = Field(..., description="会话线程ID，用于后续多轮对话")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="附加元数据（意图、情绪分等）")


class ChatStreamEvent(BaseModel):
    """流式聊天事件模型"""
    event: Literal["start", "chunk", "end", "error"] = Field(..., description="事件类型")
    thread_id: Optional[str] = Field(None, description="会话线程ID")
    content: Optional[str] = Field(None, description="内容片段（chunk事件）")
    metadata: Optional[Dict[str, Any]] = Field(None, description="元数据（end事件）")
    error: Optional[str] = Field(None, description="错误信息（error事件）")


class ChatStreamResponse(BaseModel):
    """流式聊天响应模型（用于文档）"""
    pass
