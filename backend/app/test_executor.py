"""Executor 多维验证：抽槽/失败/追问/卡片/取消/引导/上限/额外槽位"""
import asyncio
import json
import traceback

from langgraph.errors import GraphInterrupt

from app.agents.ticket.execute_agent import ticket_execute_agent
from app.agents.base import AgentInput
from app.tools.business.execution_context import (
    REQUEST_ACCESS_TOKEN_CTX,
    REQUEST_USER_ID_CTX,
    REQUEST_THREAD_ID_CTX,
)

PASS, FAIL = "✅", "❌"


async def _run(thread_id, user_query, step, existing_slots, expected_slots):
    """执行并返回 (result, is_interrupt, ask_user_args | None)"""
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
        # 检测被 React agent 吞掉的 ask_user 调用（无 ToolMessage 但有 AIMessage.tool_calls）
        tp = result.data.get("try_process", [])
        ask_calls = [e for e in tp if "args" in e and e.get("tool") == "ask_user"]
        ask_results = [e for e in tp if "result" in e and e.get("tool") == "ask_user"]
        if ask_calls and len(ask_calls) > len(ask_results):
            # ask_user 被调用但无结果 → 中断被吞
            return result, True, ask_calls[-1].get("args", {})
        return result, False, None
    except GraphInterrupt as e:
        return None, True, (e.args[0] if e.args else {})


def _a(label, verdict, *lines):
    print(f"  {PASS if verdict else FAIL} {label}")
    for line in lines:
        print(f"     {line}")
    return verdict


# ── tests ────────────────────────────────────────────────

async def test_slot_extraction():
    """维度1: 有效订单号 → 从工具结果正确抽取槽位"""
    print(f"\n{'─'*50}\n1. 正确抽槽")
    existing_slots = {"order_id": "N20260501000088"}
    step = {
        "goal": "查订单详情获取商品名",
        "completion_signal": "获取到商品名称",
        "target_slots": ["product_name"],
        "available_tools": ["get_order_detail"],
    }
    expected_slots = [
        {"name": "order_id", "description": "订单号"},
        {"name": "product_name", "description": "商品名称"},
        {"name": "channel", "description": "消费渠道"},
    ]

    result, interrupted, _ = await _run("e2e-1", "查订单N20260501000088", step, existing_slots, expected_slots)

    if interrupted:
        return _a("ask_user 触发", False, "纯查订单不应追问")

    tp = result.data.get("try_process", [])
    calls = [e["tool"] for e in tp if "args" in e]
    slots = result.data.get("slots", {})
    step_status = result.data.get("step_status")
    reason = result.data.get("reason", "")

    ok = True
    ok &= _a("调用了 get_order_detail", "get_order_detail" in str(calls), f"calls={calls}")
    ok &= _a(f"step_status={step_status}", step_status == "done", f"reason={reason}")
    ok &= _a("product_name 已填充", bool(slots.get("product_name")), str(slots.get("product_name", ""))[:80])
    ok &= _a("channel 已同步收集(非target)", bool(slots.get("channel")), str(slots.get("channel", "")))
    if result.reply:
        print(f"     reply: {result.reply[:150]}")
    return ok


async def test_fail_on_invalid():
    """维度2: 无效订单号 → 合理失败"""
    print(f"\n{'─'*50}\n2. 合理失败")
    step = {
        "goal": "查询订单详情",
        "completion_signal": "获取到订单商品信息",
        "target_slots": ["product_name", "channel"],
        "available_tools": ["get_order_detail"],
    }
    expected_slots = [
        {"name": "order_id", "description": "订单号"},
        {"name": "product_name", "description": "商品名称"},
        {"name": "channel", "description": "消费渠道"},
    ]

    result, interrupted, _ = await _run("e2e-2", "查订单INVALID99999", step, {"order_id": "INVALID99999"}, expected_slots)
    if interrupted:
        return _a("触发追问", True, "(无效订单号时追问也是合理降级)")

    step_status = result.data.get("step_status")
    reason = result.data.get("reason", "")
    return _a(f"step_status={step_status}", step_status in ("failed", "pending"), f"reason={reason}")


async def test_ask_user_basic():
    """维度3: 模糊请求 → 正确追问"""
    print(f"\n{'─'*50}\n3. 正确追问")
    step = {
        "goal": "确认退货订单",
        "completion_signal": "用户提供了订单号",
        "target_slots": ["order_id"],
        "available_tools": ["get_user_orders"],
    }
    expected_slots = [{"name": "order_id", "description": "退货订单号"}]

    result, interrupted, ask_args = await _run("e2e-3", "我想退货", step, {}, expected_slots)

    if interrupted:
        reply = ask_args.get("reply", "") if ask_args else ""
        return _a("触发了 ask_user 追问", True, f"reply={reply[:150]}")

    # 也可能直接调 get_user_orders 再追问
    tp = result.data.get("try_process", [])
    calls = [e["tool"] for e in tp if "args" in e]
    return _a("调用了工具", len(calls) > 0, f"calls={calls}")


async def test_interaction_card():
    """维度4: 追问时使用交互卡片"""
    print(f"\n{'─'*50}\n4. 交互卡片")
    step = {
        "goal": "获取订单列表并让用户选择",
        "completion_signal": "用户选择了具体订单",
        "target_slots": ["order_id"],
        "available_tools": ["get_user_orders"],
    }
    expected_slots = [{"name": "order_id", "description": "退货订单号"}]

    result, interrupted, ask_args = await _run("e2e-4", "帮我查下最近订单，我想退货", step, {}, expected_slots)

    if interrupted:
        interaction_type = ask_args.get("interaction_type", "") if ask_args else ""
        has_card = bool(interaction_type)
        return _a(f"交互卡片: {'有' if has_card else '无'}", has_card, f"interaction_type={interaction_type}")

    tp = result.data.get("try_process", [])
    ask_calls = [e for e in tp if "args" in e and e.get("tool") == "ask_user"]
    for e in ask_calls:
        if e.get("args", {}).get("interaction_type"):
            return _a("使用了 interaction_type", True, str(e["args"]["interaction_type"]))
    return _a("未使用交互卡片", False)


async def test_user_cancel():
    """维度5: 用户改变意图 → cancelled"""
    print(f"\n{'─'*50}\n5. 改变意图")
    step = {
        "goal": "确认退货订单",
        "completion_signal": "用户确认了订单号",
        "target_slots": ["order_id"],
        "available_tools": ["get_user_orders"],
    }
    expected_slots = [{"name": "order_id", "description": "退货订单号"}]

    result, interrupted, ask_args = await _run("e2e-5", "算了不退了", step, {}, expected_slots)

    if interrupted:
        reply = ask_args.get("reply", "") if ask_args else ""
        is_confirm = any(kw in reply for kw in ["取消", "确定", "確認"])
        return _a("确认取消追问", is_confirm, f"reply={reply[:150]}")

    step_status = result.data.get("step_status")
    reason = result.data.get("reason", "")
    return _a(f"step_status=cancelled", step_status == "cancelled", f"reason={reason}")


async def test_extra_slot_collection():
    """维度6: 收集非 target_slots 但在 expected_slots 中的字段"""
    print(f"\n{'─'*50}\n6. 额外槽位收集")
    existing_slots = {"order_id": "N20260501000088"}
    step = {
        "goal": "查订单详情获取商品名",
        "completion_signal": "获取到商品名称",
        "target_slots": ["product_name"],  # 只要求 product_name
        "available_tools": ["get_order_detail"],
    }
    expected_slots = [
        {"name": "order_id", "description": "订单号"},
        {"name": "product_name", "description": "商品名称"},
        {"name": "channel", "description": "消费渠道"},  # 不在 target
    ]

    result, interrupted, _ = await _run("e2e-6", "查订单N20260501000088", step, existing_slots, expected_slots)
    if interrupted:
        return _a("意外追问", False)

    slots = result.data.get("slots", {})
    ok = True
    ok &= _a("product_name (target) 已填充", bool(slots.get("product_name")), str(slots.get("product_name", ""))[:80])
    ok &= _a("channel (非target) 已同步", bool(slots.get("channel")), str(slots.get("channel", "")))
    ok &= _a(f"step_status=done", result.data.get("step_status") == "done", f"reason={result.data.get('reason', '')}")
    return ok


async def test_off_topic_guidance():
    """维度7: 用户答非所问 → 正确引导"""
    print(f"\n{'─'*50}\n7. 答非所问")
    step = {
        "goal": "确认退货订单",
        "completion_signal": "用户提供了订单号",
        "target_slots": ["order_id"],
        "available_tools": ["get_user_orders"],
    }
    expected_slots = [{"name": "order_id", "description": "退货订单号"}]

    result, interrupted, ask_args = await _run("e2e-7", "今天天气真好", step, {}, expected_slots)

    if interrupted:
        reply = ask_args.get("reply", "") if ask_args else ""
        has_guide = any(kw in reply for kw in ["订单", "退货", "帮助", "请", "提供", "退换"])
        return _a("引导回复包含业务关键词", has_guide, f"reply={reply[:200]}")

    reply = result.reply or ""
    has_guide = any(kw in reply for kw in ["订单", "退货", "帮助", "请", "提供"])
    return _a("引导回复", has_guide, f"reply={reply[:200]}")


async def test_tool_limit_closure():
    """维度8: 工具调用上限临近时 → 结束说明"""
    print(f"\n{'─'*50}\n8. 调用上限")
    step = {
        "goal": "确认退货订单和商品",
        "completion_signal": "用户确认了订单号和商品",
        "target_slots": ["order_id", "product_name"],
        "available_tools": ["get_user_orders"],  # 只给一个工具，天然限制
    }
    expected_slots = [
        {"name": "order_id", "description": "退货订单号"},
        {"name": "product_name", "description": "退货商品名称"},
    ]

    result, interrupted, ask_args = await _run("e2e-8", "我想退货但不记得订单号也不确定是哪个商品", step, {}, expected_slots)

    if interrupted:
        reply = ask_args.get("reply", "") if ask_args else ""
        return _a("触发追问(正常)", True, f"reply={reply[:200]}")

    tp = result.data.get("try_process", [])
    call_count = len([e for e in tp if "args" in e])
    reply = result.reply or ""
    has_closure = len(reply) > 0
    return _a(f"调用{call_count}次, 有结束说明", has_closure, f"reply={reply[:200]}")


# ── main ─────────────────────────────────────────────────

async def main():
    REQUEST_USER_ID_CTX.set("bonnie20260412")
    REQUEST_THREAD_ID_CTX.set("exec-e2e")
    REQUEST_ACCESS_TOKEN_CTX.set("mock-token")

    results = {}
    for name, coro in [
        ("1.正确抽槽", test_slot_extraction),
        ("2.合理失败", test_fail_on_invalid),
        ("3.正确追问", test_ask_user_basic),
        ("4.交互卡片", test_interaction_card),
        ("5.改变意图", test_user_cancel),
        ("6.额外槽位", test_extra_slot_collection),
        ("7.答非所问", test_off_topic_guidance),
        ("8.调用上限", test_tool_limit_closure),
    ]:
        try:
            results[name] = await coro()
        except Exception as e:
            print(f"\n  {FAIL} {name} 未捕获异常: {e}")
            traceback.print_exc()
            results[name] = False

    print(f"\n{'='*50}")
    print(f"  汇总: {sum(1 for v in results.values() if v)}/{len(results)} 通过")
    print(f"{'='*50}")
    for name, ok in results.items():
        print(f"  {PASS if ok else FAIL} {name}")


if __name__ == "__main__":
    asyncio.run(main())
