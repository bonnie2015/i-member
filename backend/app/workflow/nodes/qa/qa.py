import json
from typing import Any, Dict

from langchain_core.messages import AIMessage, HumanMessage

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
    entry_message = state.get("entry_message")
    current_message = messages[-1].content if messages else ""

    if not entry_message:
        entry_message = current_message

    updates: Dict[str, Any] = {
        "qa_turn_count": qa_turn_count,
        "entry_message": entry_message,
        "trace": ["识别为问答服务", "基于当前问题直接生成答复"],
    }

    if qa_turn_count > _QA_MAX_TURNS:
        coherent = await _check_coherence(entry_message, current_message)
        if not coherent:
            logger.info(f"[qa_node] incoherent after {qa_turn_count} turns, reset entry")
            updates["entry_message"] = current_message
            updates["qa_turn_count"] = 1

    final_reply = "我先帮您查一下相关政策和规则，马上回来跟您说清楚。"
    updates["final_reply"] = final_reply
    updates["messages"] = [AIMessage(content=final_reply)]
    return updates
