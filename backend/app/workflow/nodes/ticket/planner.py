from __future__ import annotations

from typing import Any, Dict, List

from app.agents.ticket.plan_agent import ticket_plan_agent
from app.agents.base import AgentInput, AgentStatus
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("ticket_plan_node")


def _build_steps(
    previous_steps: List[Dict], current_index: int, new_steps: List[Dict]
) -> List[Dict]:
    result = []
    for step in new_steps:
        if not isinstance(step, dict):
            continue
        s = dict(step)
        s["step_status"] = "pending"
        s["failed_reason"] = ""
        s["failed_type"] = ""
        s["try_process"] = []
        result.append(s)
    preserved = [dict(s) for s in previous_steps[:current_index] if isinstance(s, dict)]
    return [*preserved, *result]


async def plan_node(state: AgentState) -> Dict[str, Any]:
    service_key = str(state.get("service_key") or "").strip()
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    if not service_key:
        logger.warning("[plan_node] thread_id=%s missing_service_key", thread_id)
        return {
            "final_status": "failed",
            "final_reason": "missing_service_key",
            "current_subgraph": None,
        }

    existing_slots = dict(state.get("slots") or {})
    current_step_index = int(state.get("current_step_index") or 0)
    previous_steps = list(state.get("steps") or [])

    # 取当前步骤（如果已存在就是刚失败待重规划的步骤）
    failed_step = None
    if 0 <= current_step_index < len(previous_steps):
        fs = dict(previous_steps[current_step_index])
        if fs.get("step_status") in ("failed", "pending"):
            failed_step = fs

    logger.info(
        "[plan_node] thread_id=%s service_key=%s step_index=%s replan=%s",
        thread_id,
        service_key,
        current_step_index,
        state.get("replan_count", 0),
    )

    result = await ticket_plan_agent.run(
        AgentInput(
            user_query=str(state.get("goal") or "").strip(),
            thread_id=thread_id,
            user_id=state.get("user_id"),
            extra={
                "service_key": service_key,
                "goal": str(state.get("goal") or "").strip(),
                "current_step_index": current_step_index,
                "slots": existing_slots,
                "failed_step": failed_step,
            },
        )
    )

    if result.status != AgentStatus.SUCCESS:
        logger.error(
            "[plan_node] thread_id=%s agent_failed status=%s", thread_id, result.status
        )
        return {
            "final_status": "failed",
            "final_reason": "plan_agent_failed",
        }

    data = result.data
    steps = list(data.get("steps") or [])
    reason = str(data.get("reason") or "").strip()
    expected_slots = list(data.get("expected_slots") or [])
    pre_filled_slots = dict(data.get("slots") or {})

    logger.info(
        "[plan_node] thread_id=%s steps=%s expected_slots=%s pre_filled=%s reason=%s",
        thread_id,
        len(steps),
        len(expected_slots),
        len(pre_filled_slots),
        reason or "none",
    )

    if not steps:
        return {
            "final_status": "failed",
            "final_reason": reason or "plan_impossible",
        }

    merged_slots = {**existing_slots, **pre_filled_slots}
    built_steps = _build_steps(
        list(state.get("steps") or []),
        current_step_index,
        steps,
    )

    return {
        "steps": built_steps,
        "expected_slots": expected_slots,
        "slots": merged_slots,
        "current_step_index": current_step_index,
        "replan_reason": None,
        "goal": str(state.get("goal") or "").strip(),
    }
