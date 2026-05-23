from __future__ import annotations

import pytest
from tests.eval.runner import EvalRunner


async def _seed_and_run_user_facts(case, runner):
    """预 seed Redis 后调 user_facts_agent.run()。"""
    from app.memory.user_facts import save_user_facts_store

    overrides = case.get("setup", {}).get("state_overrides", {})
    seed = overrides.get("seed_facts") or []
    user_id = overrides.get("user_id", "test_user_001")

    if seed:
        await save_user_facts_store(user_id, add_facts=seed, delete_facts=[])

    return await runner.run_case(case)


@pytest.mark.asyncio
async def test_user_facts(load_case):
    case_list = load_case("regression/agent/agent_user_facts.json")
    cases = case_list if isinstance(case_list, list) else [case_list]
    runner = EvalRunner(case_dirs=["regression"])

    for case in cases:
        result = await _seed_and_run_user_facts(case, runner)
        assert result.passed, _format_failure(result)


@pytest.mark.asyncio
async def test_summary():
    """直接调 summary_agent.summarize_service，验证不同 intent 的总结质量。"""
    from app.agents.summary_agent import summary_agent
    from langchain_core.messages import HumanMessage, AIMessage

    cases = [
        {
            "id": "pp_001",
            "intent": "ticket",
            "messages": [
                HumanMessage(content="我要退货，订单号 ORD-TEST-001，颜色不喜欢"),
                AIMessage(content="好的，我帮您查一下订单 ORD-TEST-001"),
                HumanMessage(content="对，就是这双 MEXICO 66 SD 米色"),
                AIMessage(content="已为您创建退货工单 TK202605210001"),
            ],
            "keywords": ["退货", "ORD-TEST-001"],
        },
        {
            "id": "pp_002",
            "intent": "qa",
            "messages": [
                HumanMessage(content="退换货政策是什么"),
                AIMessage(content="Onitsuka Tiger支持7天无理由退货，商品须保持原状"),
            ],
            "keywords": ["退换货"],
        },
        {
            "id": "pp_003",
            "intent": "recommend",
            "messages": [
                HumanMessage(content="有没有适合跑步的鞋"),
                AIMessage(content="为您推荐 MEXICO 66 SD 休闲鞋 米色/黑色款"),
                HumanMessage(content="有没有白色的"),
                AIMessage(content="为您推荐 MEXICO 66 休闲鞋 白色款"),
            ],
            "keywords": ["MEXICO"],
        },
    ]

    for c in cases:
        summary = await summary_agent.summarize_service(
            messages=c["messages"],
            intent=c["intent"],
            thread_id="test",
            user_id="test_user_001",
        )
        assert summary, f"[{c['id']}] summary empty"
        for kw in c["keywords"]:
            assert kw.lower() in summary.lower(), (
                f"[{c['id']}] summary missing '{kw}': {summary[:100]}"
            )


def _format_failure(result) -> str:
    return (
        f"[{result.task_id}] FAILED ({result.passed_trials}/{result.trials}): "
        + "; ".join(
            f"{r['check']}: {r['detail']}"
            for tr in result.trial_results
            for r in tr.code_results
            if not r["passed"]
        )
    )
