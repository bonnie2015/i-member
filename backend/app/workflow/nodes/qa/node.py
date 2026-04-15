import json
from typing import Any, Dict

from langchain_core.messages import HumanMessage

from app.agents.llm.llm_factory import get_local_llm
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("qa_node")
_QA_MAX_TURNS = 5

_QA_COHERENCE_PROMPT = (
    "判断以下两条消息是否属于同一个话题或连贯的对话。\n"
    "参考消息（对话起点）：{entry}\n"
    "当前消息：{current}\n"
    "仅输出 JSON：{{\"coherent\": true}} 或 {{\"coherent\": false}}"
)


async def _check_coherence(entry_message: str, current_message: str) -> bool:
    try:
        llm = get_local_llm(role="router")
        prompt = _QA_COHERENCE_PROMPT.format(entry=entry_message, current=current_message)
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = str(resp.content).strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            return json.loads(raw[start:end]).get("coherent", True)
    except Exception as e:
        logger.warning(f"[qa_node] coherence check failed: {e}")
    return True


async def qa_node(state: AgentState) -> Dict[str, Any]:
    messages = state.get("messages", [])
    qa_turn_count = state.get("qa_turn_count", 0) + 1
    service_entry = state.get("service_entry_message")
    user_context = state.get("user_context") or {}
    current_message = messages[-1].content if messages else ""

    if not service_entry:
        service_entry = current_message

    updates: Dict[str, Any] = {
        "qa_turn_count": qa_turn_count,
        "service_entry_message": service_entry,
    }

    if qa_turn_count > _QA_MAX_TURNS:
        coherent = await _check_coherence(service_entry, current_message)
        if not coherent:
            logger.info(f"[qa_node] incoherent after {qa_turn_count} turns, reset entry")
            updates["service_entry_message"] = current_message
            updates["qa_turn_count"] = 1

    user_info = ""
    profile = user_context.get("profile") or {}
    name = profile.get("name", "")
    level = profile.get("member_level", "")
    if name:
        user_info = f"（您好 {name}，{level}）"

    updates["final_reply"] = f"我先帮您查一下相关政策和规则{user_info}，马上回来跟您说清楚。"
    return updates
