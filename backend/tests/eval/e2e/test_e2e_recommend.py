from __future__ import annotations

import pytest
from tests.eval.runner import EvalRunner


@pytest.mark.asyncio
async def test_e2e_recommend(load_case, category):
    case_list = load_case(f"{category}/e2e/e2e_recommend.json")
    cases = case_list if isinstance(case_list, list) else [case_list]
    runner = EvalRunner(case_dirs=[category])

    for case in cases:
        result = await runner.run_case(case)
        code_fails = [
            f"{r['check']}: {r['detail']}"
            for tr in result.trial_results
            for r in tr.code_results
            if not r["passed"]
        ]
        model_fails = []
        for tr in result.trial_results:
            if tr.model_results:
                for dim, d in tr.model_results.items():
                    if d.get("score", 0) < 2:
                        model_fails.append(
                            f"{dim}={d.get('score')}: {d.get('reasoning', '')[:80]}"
                        )
        assert result.passed, (
            f"[{result.task_id}] FAILED ({result.passed_trials}/{result.trials})\n"
            f"  Code: {'; '.join(code_fails) if code_fails else 'all passed'}\n"
            f"  Model: {'; '.join(model_fails) if model_fails else 'all passed'}"
        )
