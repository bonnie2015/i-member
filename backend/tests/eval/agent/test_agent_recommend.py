from __future__ import annotations

import pytest
from tests.eval.runner import EvalRunner


@pytest.mark.asyncio
async def test_recommend_agent(load_case):
    """Run all recommend regression cases."""
    case_list = load_case("regression/agent/agent_recommend.json")
    cases = case_list if isinstance(case_list, list) else [case_list]

    runner = EvalRunner(case_dirs=["regression"])

    for case in cases:
        if case.get("agent") != "recommend":
            continue
        result = await runner.run_case(case)
        assert result.passed, (
            f"[{result.task_id}] FAILED ({result.passed_trials}/{result.trials}): "
            + "; ".join(
                f"{r['check']}: {r['detail']}"
                for tr in result.trial_results
                for r in tr.code_results
                if not r["passed"]
            )
        )
