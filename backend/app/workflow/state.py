from typing import List, Dict, Any, Optional, Literal, TypedDict
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


class AgentState(TypedDict, total=False):
    # 基础信息
    user_id: str
    thread_id: str
    channel: Literal["wechat", "app", "web", "jd", "tmall", "douyin", "api"]

    user_context: Optional[Dict[str, Any]]

    messages: List[BaseMessage]
    final_reply: Optional[str]
    trace: List[Any]
    started_at: Optional[str]

    # 意图路由
    intent: Optional[Literal["ticket", "qa", "recommend"]]
    reason: Optional[str]

    # 当前子图（有值时优先进入该子图，跳过意图识别）
    current_subgraph: Optional[Literal["ticket", "qa", "recommend"]]

    # 子图内部状态（各子图自行读写，主图不关心内容）
    service_state: Optional[Dict[str, Any]]

    # ticket 运行态
    guard_decision: Optional[str]
    service_key: Optional[str]
    goal: Optional[str]
    steps: List[Dict[str, Any]]
    current_step_index: int
    expected_slots: List[Dict[str, Any]]
    replan_count: int
    planner_reason: Optional[str]
    slots: Optional[Dict[str, Any]]
    final_status: Optional[Literal["success", "failed", "cancelled"]]
    final_reason: Optional[str]


def extract_last_service_round(messages: List[BaseMessage]) -> List[BaseMessage]:
    """提取已完成服务的最后一轮对话（用户消息 + 无工具调用的 AI 回复）。

    若最后一条消息是 AI → 最后一轮已完整，取最后一个 Human + 之后 AI。
    若最后一条消息是 Human → 该消息尚未被回复（属于下一服务），取倒数第二个 Human。
    """
    human_indices = [i for i, m in enumerate(messages) if isinstance(m, HumanMessage)]
    if not human_indices:
        return []

    last_is_ai = isinstance(messages[-1], AIMessage) if messages else False

    if last_is_ai:
        # 最后一轮完整：取最后一个 Human 及其后的 AI
        start_idx = human_indices[-1]
        end_idx = len(messages)
    else:
        # 最后一条 Human 未被回复（属于下一服务）：取倒数第二个 Human
        if len(human_indices) < 2:
            return []
        start_idx = human_indices[-2]
        end_idx = human_indices[-1]

    result: List[BaseMessage] = [messages[start_idx]]
    for i in range(start_idx + 1, end_idx):
        msg = messages[i]
        if isinstance(msg, AIMessage):
            content = str(getattr(msg, "content", "") or "").strip()
            if content and not list(getattr(msg, "tool_calls", None) or []):
                result.append(msg)
    return result


def get_service_clear_state() -> Dict[str, Any]:
    """返回清除所有服务运行态字段的 state 更新模板。"""
    return {
        "intent": None,
        "reason": None,
        "current_subgraph": None,
        "final_reply": None,
        "final_status": None,
        "final_reason": None,
        "trace": [],
        "started_at": None,
        "service_state": None,
        "service_key": None,
        "goal": None,
        "steps": [],
        "current_step_index": 0,
        "expected_slots": [],
        "replan_count": 0,
        "planner_reason": None,
        "slots": None,
        "guard_decision": None,
        "messages": [],
    }
