from enum import Enum
from typing import List, Dict, Any, Optional, Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class TicketNextAction(str, Enum):
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
    tool_messages: Annotated[List[BaseMessage], add_messages]
    final_reply: Optional[str]
    recommended_products: List[Dict[str, Any]]

    # 意图路由
    intent: Optional[Literal["ticket", "qa", "recommend"]]
    reason: Optional[str]

    # 当前子图（有值时优先进入该子图，跳过意图识别）
    current_subgraph: Optional[Literal["ticket", "qa", "recommend"]]

    # 推荐子图专用
    recommend_loop: int
    recommend_context: Optional[Dict[str, Any]]

    # QA 专用
    qa_turn_count: int
    entry_message: Optional[str]  # 首条消息，连贯性检测基准

    # ticket 运行态
    service_key: Optional[str]
    goal: Optional[str]
    steps: List[Dict[str, Any]]
    current_step_index: int
    expected_slots: List[str]
    next_action: Optional[TicketNextAction]
    replan_count: int
    slots: Optional[Dict[str, Any]]
    final_status: Optional[Literal["success", "failed", "cancelled"]]
    final_reason: Optional[str]
    trace: List[Any]
    started_at: Optional[str]


class TicketState(AgentState, total=False):
    pass


def first_pending_step_index(steps: List[Dict[str, Any]]) -> Optional[int]:
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if str(step.get("step_status") or "pending").strip() != "successed":
            return index
    return None


def normalize_current_step_index(
    steps: List[Dict[str, Any]],
    current_step_index: int,
) -> Optional[int]:
    if not steps:
        return None

    if 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        if isinstance(step, dict) and str(step.get("step_status") or "pending").strip() != "successed":
            return current_step_index

    return first_pending_step_index(steps)


def is_valid_current_step_index(
    steps: List[Dict[str, Any]],
    current_step_index: int,
) -> bool:
    if 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        if isinstance(step, dict) and str(step.get("step_status") or "pending").strip() != "successed":
            return True
    return False


def resolve_current_step_index(
    state: AgentState,
    steps: List[Dict[str, Any]],
) -> Optional[int]:
    state_index = int(state.get("current_step_index") or 0)

    if not steps:
        if state_index != 0:
            state["current_step_index"] = 0
        return None

    if is_valid_current_step_index(steps, state_index):
        return state_index

    corrected_index = normalize_current_step_index(steps, state_index)
    if corrected_index is not None:
        if corrected_index != state_index:
            state["current_step_index"] = corrected_index
        return corrected_index

    completed_index = len(steps)
    if state_index != completed_index:
        state["current_step_index"] = completed_index
    return completed_index
