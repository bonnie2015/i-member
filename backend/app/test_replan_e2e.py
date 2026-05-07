"""Replan 端到端验证：executor→reflect→plan 闭环 + 次数上限"""
import asyncio
import json

from app.workflow.state import AgentState
from app.workflow.nodes.ticket.graph import get_ticket_workflow, reflect_node, _route_after_reflect
from app.tools.business.execution_context import (
    REQUEST_ACCESS_TOKEN_CTX,
    REQUEST_USER_ID_CTX,
    REQUEST_THREAD_ID_CTX,
    business_execution_context,
)

ticket_graph = get_ticket_workflow()


def _empty_state(**overrides) -> AgentState:
    s: AgentState = {
        "user_id": "bonnie20260412",
        "thread_id": "replan-e2e",
        "channel": "app",
        "messages": [],
        "steps": [],
        "current_step_index": 0,
        "replan_count": 0,
        "slots": {},
        "expected_slots": [],
        "user_context": {"member_level": "黄金会员"},
    }
    s.update(overrides)
    return s


async def test_replan_single_cycle():
    """退货请求完整跑通或正常中断（ask_user），验证无异常 replan"""
    print("1. 正常场景：退货请求")

    from langchain_core.messages import HumanMessage
    state = _empty_state()
    state["messages"] = [HumanMessage(content="帮我查下最近订单，我想退之前买的鞋子")]
    state["service_key"] = "refund"
    state["goal"] = "帮用户完成退货"

    try:
        with business_execution_context(thread_id="replan-1", user_id="bonnie20260412"):
            result = await ticket_graph.ainvoke(state)
        final_status = result.get("final_status") or "interrupted"
        replan_count = result.get("replan_count", 0)
        steps = result.get("steps") or []
        step_statuses = [s.get("step_status") for s in steps if isinstance(s, dict)]
        print(f"  final_status={final_status} replan={replan_count} steps={len(steps)} statuses={step_statuses}")
        print(f"  {'✅ 正常' if final_status in ('success', 'interrupted') and replan_count == 0 else '⚠️ 意外replan'}")
    except Exception as e:
        if "GraphInterrupt" in type(e).__name__:
            print(f"  ⚠️ 子图内部中断（未正确处理）")
        else:
            print(f"  ❌ 异常: {type(e).__name__}: {e}")


async def test_replan_on_failure():
    """缺失关键信息场景，预期走 interrupt 后继续"""
    print("\n2. 模糊请求场景：仅说'我想退货'")

    from langchain_core.messages import HumanMessage
    state = _empty_state()
    state["service_key"] = "refund"
    state["goal"] = "帮用户完成退货"
    state["messages"] = [HumanMessage(content="我想退货")]

    try:
        with business_execution_context(thread_id="replan-2", user_id="bonnie20260412"):
            result = await ticket_graph.ainvoke(state)
        steps = result.get("steps") or []
        replan_count = result.get("replan_count", 0)
        final_status = result.get("final_status") or "interrupted"
        step_statuses = [s.get("step_status") for s in steps if isinstance(s, dict)]
        print(f"  final_status={final_status} replan={replan_count} steps={len(steps)} statuses={step_statuses}")
        print(f"  ✅ 完成" if final_status in ("success", "interrupted") else f"  ⚠️ {final_status}")
    except Exception as e:
        if "GraphInterrupt" in type(e).__name__:
            print(f"  ⚠️ 子图内部中断（未正确处理）")
        else:
            print(f"  ❌ 异常: {type(e).__name__}: {e}")


async def test_replan_limit():
    """reflect_node 写入决策 + _route_after_reflect 路由，验证上限保护"""
    print("\n3. Replan 上限验证（reflect_node + _route_after_reflect）")

    steps = [{"goal": "step1", "step_status": "pending", "failed_reason": "need more info"}]

    # 第1次：reflect_node 写 planner_reason → route to "plan"
    s1 = _empty_state(steps=steps, current_step_index=0, replan_count=0)
    update1 = reflect_node(s1)
    assert update1["planner_reason"] == "need more info"
    assert update1["replan_count"] == 1
    goto = _route_after_reflect({**s1, **update1})
    assert goto == "plan", f"pending 应路由到 plan，实际 {goto}"
    print(f"  ✅ replan=0 + pending → reflect 写 planner_reason, route → plan")

    # 第2次：reflect_node 仍然写 planner_reason
    s2 = _empty_state(steps=steps, current_step_index=0, replan_count=1)
    update2 = reflect_node(s2)
    assert update2["planner_reason"] == "need more info"
    assert update2["replan_count"] == 2
    goto2 = _route_after_reflect({**s2, **update2})
    assert goto2 == "plan"
    print(f"  ✅ replan=1 + pending → reflect 写 planner_reason, route → plan")

    # 第3次：超出上限 → reflect_node 写 final_status=failed
    s3 = _empty_state(steps=steps, current_step_index=0, replan_count=2)
    update3 = reflect_node(s3)
    assert update3["final_status"] == "failed"
    goto3 = _route_after_reflect({**s3, **update3})
    assert goto3 == "finalize"
    print(f"  ✅ replan=2 + pending → reflect 写 final_status, route → finalize，上限保护生效")

    # done 正常结束
    steps_done = [{"goal": "step1", "step_status": "done"}]
    s4 = _empty_state(steps=steps_done, current_step_index=0)
    update4 = reflect_node(s4)
    assert update4["final_status"] == "success"
    goto4 = _route_after_reflect({**s4, **update4})
    assert goto4 == "finalize"
    print(f"  ✅ done → reflect 写 final_status, route → finalize")


async def main():
    REQUEST_USER_ID_CTX.set("bonnie20260412")
    REQUEST_THREAD_ID_CTX.set("replan-e2e")
    REQUEST_ACCESS_TOKEN_CTX.set("mock-token")

    await test_replan_single_cycle()
    await test_replan_on_failure()
    await test_replan_limit()

    print(f"\n{'='*50}")
    print("  Replan 端到端验证完成")


if __name__ == "__main__":
    asyncio.run(main())
