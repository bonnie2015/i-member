from typing import List, Dict, Any, Optional, Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    # 基础信息
    user_id: str
    thread_id: str
    channel: Literal["wechat", "app", "web", "jd", "tmall", "douyin", "api"]

    user_context: Optional[Dict[str, Any]]

    messages: Annotated[List[BaseMessage], add_messages]
    tool_messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]

    # 意图路由
    intent: Optional[Literal["ticket", "qa", "recommend"]]
    reason: Optional[str]

    # QA 专用
    qa_turn_count: int
    entry_message: Optional[str]  # 首条消息，连贯性检测基准

    # ticket 运行态
    service_key: Optional[str]
    goal: Optional[str]
    ticket_loop_count: int
    slots: Optional[Dict[str, Any]]
    final_status: Optional[Literal["success", "failed", "cancelled"]]
    final_reason: Optional[str]
    trace: List[Any]
    started_at: Optional[str]
