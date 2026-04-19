from typing import List, Dict, Any, Optional, Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    # 基础信息
    user_id: str
    thread_id: str
    channel: Literal["wechat", "app", "web", "jd", "tmall", "douyin", "api"]
    messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]

    # 意图路由
    intent: Optional[Literal["ticket", "qa", "recommend"]]
    reason: Optional[str]
    is_continuous: bool
    is_direct_reply: bool

    # QA 专用
    qa_turn_count: int
    service_entry_message: Optional[str]  # 首条消息，连贯性检测基准

    # 情绪
    emotion_score: Optional[float]  # 0=极负面，1=极正面

    # 用户上下文（服务前通过接口预加载）
    user_context: Optional[Dict[str, Any]]  # 包含 profile + level + tags + long_term_memories

    # Token 监控（各节点累加）
    token_usage_total: int


class TicketState(AgentState, total=False):
    service_key: Optional[str]
    skill_location: Optional[str]
    current_goal: Optional[str]
    available_tools: List[str]
    slots: Optional[Dict[str, Any]]
    final_status: Optional[Literal["success", "failed", "cancelled"]]
    final_reason: Optional[str]
