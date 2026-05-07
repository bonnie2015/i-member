"""Reflect 路由逻辑验证：reflect_node 写入决策 + _route_after_reflect 只读返回字符串"""
import asyncio
from typing import Any, Dict, List

from app.workflow.nodes.ticket.graph import reflect_node, _route_after_reflect, _MAX_REPLAN
from app.workflow.state import AgentState


def _state(
    *,
    steps: List[Dict[str, Any]] | None = None,
    current_step_index: int = 0,
    replan_count: int = 0,
    final_status: str = "",
    planner_reason: str = "",
) -> AgentState:
    s: AgentState = {
        "current_step_index": current_step_index,
        "replan_count": replan_count,
    }
    if steps is not None:
        s["steps"] = steps
    if final_status:
        s["final_status"] = final_status
    if planner_reason:
        s["planner_reason"] = planner_reason
    return s


def _merge(base: AgentState, update: dict) -> AgentState:
    merged = dict(base)
    merged.update(update)
    return merged


def test_done_has_next():
    """done + 还有下一步 → reflect 写 current_step_index+1, replan_count=0 → route 到 executor"""
    steps = [
        {"goal": "step1", "step_status": "done"},
        {"goal": "step2", "step_status": "pending"},
    ]
    s = _state(steps=steps, current_step_index=0)
    update = reflect_node(s)
    assert update == {"current_step_index": 1, "replan_count": 0}, f"unexpected: {update}"
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "executor", f"expected executor, got {goto}"
    print("  ✅ done + next → reflect 推进 index, route → executor")


def test_done_last_step():
    """done + 最后一步 → reflect 写 final_status=success → route 到 finalize"""
    steps = [{"goal": "last", "step_status": "done"}]
    s = _state(steps=steps, current_step_index=0)
    update = reflect_node(s)
    assert update == {"final_status": "success"}, f"unexpected: {update}"
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "finalize"
    print("  ✅ done + last → reflect 写 final_status, route → finalize")


def test_cancelled():
    """cancelled → reflect 写 final_status=cancelled → route 到 finalize"""
    steps = [{"goal": "step1", "step_status": "cancelled"}]
    s = _state(steps=steps, current_step_index=0)
    update = reflect_node(s)
    assert update == {"final_status": "cancelled"}, f"unexpected: {update}"
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "finalize"
    print("  ✅ cancelled → finalize")


def test_pending_replan():
    """pending + under limit → reflect 写 planner_reason, replan_count+1 → route 到 plan"""
    steps = [{"goal": "step1", "step_status": "pending", "failed_reason": "need more info"}]
    s = _state(steps=steps, current_step_index=0, replan_count=0)
    update = reflect_node(s)
    assert update["replan_count"] == 1
    assert update["planner_reason"] == "need more info"
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "plan"
    print("  ✅ pending + under limit → plan")


def test_replan_limit():
    """replan >= MAX → reflect 写 final_status=failed → route 到 finalize"""
    steps = [{"goal": "step1", "step_status": "failed", "failed_reason": "死循环"}]
    s = _state(steps=steps, current_step_index=0, replan_count=_MAX_REPLAN)
    update = reflect_node(s)
    assert update["final_status"] == "failed"
    assert "死循环" in str(update.get("final_reason", ""))
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "finalize"
    print("  ✅ replan limit → finalize")


def test_no_steps():
    """空 steps → reflect 写 final_status=success → route 到 finalize"""
    s = _state(steps=[], current_step_index=0)
    update = reflect_node(s)
    assert update == {"final_status": "success"}, f"unexpected: {update}"
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "finalize"
    print("  ✅ no steps → finalize")


def test_index_out_of_range():
    """index 越界 → reflect 写 final_status=success → route 到 finalize"""
    steps = [{"goal": "only", "step_status": "done"}]
    s = _state(steps=steps, current_step_index=10)
    update = reflect_node(s)
    assert update == {"final_status": "success"}, f"unexpected: {update}"
    goto = _route_after_reflect(_merge(s, update))
    assert goto == "finalize"
    print("  ✅ index out of range → finalize")


async def main():
    tests = [
        ("done+next", test_done_has_next),
        ("done+last", test_done_last_step),
        ("cancelled", test_cancelled),
        ("pending→replan", test_pending_replan),
        ("replan→limit", test_replan_limit),
        ("no steps", test_no_steps),
        ("index out of range", test_index_out_of_range),
    ]

    passed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")

    print(f"\n{'='*40}")
    print(f"  Reflect: {passed}/{len(tests)} 通过")
    print(f"{'='*40}")


if __name__ == "__main__":
    asyncio.run(main())
