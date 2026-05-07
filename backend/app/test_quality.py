"""验证：交互卡片拼装 + 分页筛选"""
import asyncio
import json

from langgraph.errors import GraphInterrupt

from app.agents.ticket.execute_agent import ticket_execute_agent
from app.agents.base import AgentInput
from app.tools.business.execution_context import (
    REQUEST_ACCESS_TOKEN_CTX,
    REQUEST_USER_ID_CTX,
    REQUEST_THREAD_ID_CTX,
)


async def _run(thread_id, user_query, step, existing_slots, expected_slots):
    try:
        result = await ticket_execute_agent.run(AgentInput(
            user_query=user_query,
            thread_id=thread_id,
            user_id="bonnie20260412",
            extra={
                "step": step,
                "slots": existing_slots,
                "expected_slots": expected_slots,
            },
        ))
        tp = result.data.get("try_process", [])
        ask_calls = [e for e in tp if "args" in e and e.get("tool") == "ask_user"]
        ask_results = [e for e in tp if "result" in e and e.get("tool") == "ask_user"]
        interrupted = ask_calls and len(ask_calls) > len(ask_results)
        ask_args = ask_calls[-1].get("args", {}) if ask_calls else {}
        return result, interrupted, ask_args, tp
    except GraphInterrupt as e:
        payload = e.args[0] if e.args else {}
        return None, True, payload, []


async def test_interaction_card_assembly():
    """交互卡片是否正确拼装（entities 来自工具结果）"""
    print("=" * 60)
    print("1. 交互卡片拼装验证")
    print("=" * 60)

    step = {
        "goal": "获取订单列表并让用户选择要退货的订单",
        "completion_signal": "用户选择了具体订单",
        "target_slots": ["order_id"],
        "available_tools": ["get_user_orders"],
    }
    expected_slots = [{"name": "order_id", "description": "退货订单号"}]

    result, interrupted, ask_args, tp = await _run(
        "qa-1", "帮我查下最近订单，我想退货",
        step, {}, expected_slots,
    )

    # 检查工具调用
    calls = [e for e in tp if "args" in e]
    print(f"\n工具调用链:")
    for e in tp:
        if "args" in e:
            print(f"  CALL {e['tool']}: {json.dumps(e['args'], ensure_ascii=False)[:200]}")
        else:
            print(f"  RESULT {e['tool']}: {e.get('result', '')[:200]}")

    # 检查分页
    get_orders_call = next((e for e in calls if e["tool"] == "get_user_orders"), None)
    if get_orders_call:
        args = get_orders_call["args"]
        has_page = "page" in args
        page_size = args.get("page_size", "NOT_SET")
        print(f"\n分页: page={args.get('page')}, page_size={page_size}")
        print(f"  {'✅ 有分页' if has_page else '❌ 无分页——可能全量查询!'}")
        print(f"  {'✅ page_size合理(<=20)' if isinstance(page_size, int) and page_size <= 20 else '⚠️ page_size=' + str(page_size)}")

    # 检查交互卡片
    print(f"\nask_user 参数:")
    print(f"  reply: {ask_args.get('reply', '')[:200]}")
    interaction_type = ask_args.get("interaction_type")
    print(f"  interaction_type: {interaction_type}")

    if interrupted and hasattr(result, '__class__'):
        pass  # interrupted via GraphInterrupt
    elif interrupted:
        pass  # swallowed interrupt

    if interaction_type == "select_order":
        print("  ✅ 使用了 select_order 交互卡片")
    elif interaction_type:
        print(f"  ✅ 使用了 {interaction_type}")
    else:
        print("  ❌ 未使用交互卡片，纯文本追问")


async def test_pagination_and_filter():
    """分页和筛选行为验证"""
    print("\n" + "=" * 60)
    print("2. 分页 + 筛选验证")
    print("=" * 60)

    step = {
        "goal": "确认退货订单",
        "completion_signal": "用户确认了订单号",
        "target_slots": ["order_id"],
        "available_tools": ["get_user_orders"],
    }
    expected_slots = [{"name": "order_id", "description": "退货订单号"}]

    # 场景A: 查订单列表
    result, interrupted, ask_args, tp = await _run(
        "qa-2a", "帮我查下我的订单，我想退货之前买的鞋子",
        step, {}, expected_slots,
    )

    calls = [e for e in tp if "args" in e]
    get_orders_calls = [e for e in calls if e["tool"] == "get_user_orders"]
    print(f"\n场景A: 查鞋子退货 → {len(get_orders_calls)}次 get_user_orders")
    for c in get_orders_calls:
        print(f"  args: {json.dumps(c['args'], ensure_ascii=False)}")

    # 场景B: 具体时间范围
    result2, interrupted2, ask_args2, tp2 = await _run(
        "qa-2b", "帮我查5月份的订单",
        step, {}, expected_slots,
    )

    calls2 = [e for e in tp2 if "args" in e]
    get_orders_calls2 = [e for e in calls2 if e["tool"] == "get_user_orders"]
    print(f"\n场景B: 5月份订单 → {len(get_orders_calls2)}次 get_user_orders")
    for c in get_orders_calls2:
        print(f"  args: {json.dumps(c['args'], ensure_ascii=False)}")

    # 汇总
    all_calls = get_orders_calls + get_orders_calls2
    has_pagination = all("page" in c.get("args", {}) for c in all_calls if all_calls)
    print(f"\n{'✅ 所有列表查询都带分页' if has_pagination else '❌ 存在无分页的列表查询'}")


async def main():
    REQUEST_USER_ID_CTX.set("bonnie20260412")
    REQUEST_THREAD_ID_CTX.set("qa-thread")
    REQUEST_ACCESS_TOKEN_CTX.set("mock-token")

    await test_interaction_card_assembly()
    await test_pagination_and_filter()


if __name__ == "__main__":
    asyncio.run(main())
