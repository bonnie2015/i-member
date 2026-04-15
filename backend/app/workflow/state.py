from enum import Enum
from typing import List, Dict, Any, Optional, Annotated, Literal, TypedDict
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage

class TicketNextAction(str, Enum):
    PLAN = "plan"
    EXECUTOR = "executor"
    REFLECT = "reflect"
    END = "end"


TicketScene = Literal["refund", "change", "quality", "complain", "equity", "others"]


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

    ticket_scene: Optional[TicketScene]
    current_goal: Optional[str]
    selected_skill_content: Optional[str]
    steps: List[Dict[str, Any]]
    current_step_index: int  # 当前停留的步骤索引：executor 执行它；失败时保持；继续下一步时才推进
    slots: Optional[Dict[str, Any]]
    expected_slots: List[str]  # 本轮计划全程预期收集的槽位 key 聚合（所有步骤 target_slots 的并集）

    next_action: Optional[TicketNextAction]
    replan_count: int
    final_status: Optional[Literal["success", "failed", "cancelled"]]
    final_reason: Optional[str]


def first_pending_step_index(steps: List[Dict[str, Any]]) -> Optional[int]:
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if not bool(step.get("is_success")):
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
        if isinstance(step, dict) and not bool(step.get("is_success")):
            return current_step_index

    pending_index = first_pending_step_index(steps)
    if pending_index is not None:
        return pending_index
    return None
