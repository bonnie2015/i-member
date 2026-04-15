"""
ticket_reflect.py — reflect 节点

规则：
- current_step_index 是当前步骤在 steps 中的索引
- 当 current_step_index 错位时，重新检查第一个 is_success=false 的步骤并修正
- 当前步骤成功 -> 下一步或完成
- 当前步骤失败 -> plan
- 不在 reflect 中重试 executor
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.config.logging import get_logger
from app.workflow.state import TicketNextAction, TicketState

logger = get_logger("ticket_reflect")


def _log_decision(label: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    logger.info("[reflect_node] %s=%s", label, payload)
    return payload


def _resolve_current_step(steps: List[Dict[str, Any]], current_step_index: int) -> Tuple[Optional[int], Dict[str, Any]]:
    if not steps:
        return None, {}
    if 0 <= current_step_index < len(steps):
        step = steps[current_step_index]
        if isinstance(step, dict):
            return current_step_index, step
    return None, {}


def _first_pending_index(steps: List[Dict[str, Any]]) -> int:
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        if not bool(step.get("is_success")):
            return index
    return -1


def _repair_current_step(steps: List[Dict[str, Any]]) -> Optional[int]:
    if not steps:
        return None

    first_pending = _first_pending_index(steps)
    if first_pending < 0:
        repaired = len(steps) - 1
        logger.info("[reflect_node] all steps are successful, repaired current_step_index=%s", repaired)
        return repaired

    repaired = first_pending
    logger.info(
        "[reflect_node] repaired current_step_index to first_pending_index=%s",
        repaired,
    )
    return repaired


async def reflect_node(state: TicketState) -> Dict[str, Any]:
    steps = state.get("steps") or []
    current_step_index = int(state.get("current_step_index", 0))

    try:
        if not steps:
            return _log_decision("decision", {
                "next_action": TicketNextAction.FINALIZE,
                "final_status": "failed",
                "final_reason": "no_executable_plan",
            })

        current_index, current_step_data = _resolve_current_step(steps, current_step_index)
        if current_index is None:
            repaired_step = _repair_current_step(steps)
            if repaired_step is None:
                return _log_decision("decision", {
                    "next_action": TicketNextAction.FINALIZE,
                    "current_step_index": current_step_index,
                    "final_status": "success",
                    "final_reason": "completed",
                })
            return _log_decision("decision", {
                "current_step_index": repaired_step,
                "next_action": TicketNextAction.EXECUTOR,
                "final_status": None,
                "final_reason": None,
            })

        result = current_step_data.get("result") or {}
        if isinstance(result, dict) and bool(result.get("cancelled")):
            return _log_decision("decision", {
                "next_action": TicketNextAction.FINALIZE,
                "final_status": "cancelled",
                "final_reason": str(result.get("reason") or "user_cancelled"),
            })
        if not bool(current_step_data.get("is_success")) and isinstance(result, dict) and bool(result.get("unsolvable")):
            return _log_decision("decision", {
                "next_action": TicketNextAction.FINALIZE,
                "final_status": "failed",
                "final_reason": "executor_unsolvable",
            })

        if not bool(current_step_data.get("is_success")):
            return _log_decision("decision", {
                "next_action": TicketNextAction.PLAN,
                "current_step_index": current_index,
                "replan_count": int(state.get("replan_count", 0)) + 1,
                "final_status": None,
                "final_reason": None,
            })

        next_index = current_index + 1
        if next_index < len(steps):
            return _log_decision("decision", {
                "next_action": TicketNextAction.EXECUTOR,
                "current_step_index": next_index,
                "replan_count": 0,
                "final_status": None,
                "final_reason": None,
            })

        return _log_decision("decision", {
            "next_action": TicketNextAction.FINALIZE,
            "current_step_index": current_index,
            "final_status": "success",
            "final_reason": "completed",
        })
    except Exception as exc:
        logger.exception("[reflect_node] unexpected error: %s", exc)
        return {
            "next_action": TicketNextAction.FINALIZE,
            "final_status": "failed",
            "final_reason": "reflect_exception",
        }
