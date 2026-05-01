from enum import Enum
from typing import List, Dict, Any, Optional, Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class TicketNextNode(str, Enum):
    PLAN = "plan"
    EXECUTOR = "executor"
    REFLECT = "reflect"
    END = "end"


class AgentState(TypedDict, total=False):
    # 基础信息
    user_id: str
    thread_id: str
    channel: Literal["wechat", "app", "web", "jd", "tmall", "douyin", "api"]

    user_context: Optional[Dict[str, Any]]

    messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]
    trace: List[Any]
    started_at: Optional[str]

    # 意图路由
    intent: Optional[Literal["ticket", "qa", "recommend", "direct_reply"]]
    reason: Optional[str]

    # 当前子图（有值时优先进入该子图，跳过意图识别）
    current_subgraph: Optional[Literal["ticket", "qa", "recommend"]]

    # 推荐子图专用
    recommend_loop: int
    recommend_context: Optional[Dict[str, Any]]
    recommended_products: List[Dict[str, Any]]

    # QA 专用
    qa_turn_count: int
    entry_message: Optional[str]  # 首条消息，连贯性检测基准

    # ticket 运行态
    service_key: Optional[str]
    goal: Optional[str]
    steps: List[Dict[str, Any]]
    current_step_index: int
    expected_slots: List[str]
    ticket_next_node: Optional[TicketNextNode]
    executor_retry_count: int
    replan_count: int
    planner_reason: Optional[str]
    slots: Optional[Dict[str, Any]]
    final_status: Optional[Literal["success", "failed", "cancelled"]]
    final_reason: Optional[str]
