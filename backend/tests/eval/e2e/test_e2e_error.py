from __future__ import annotations

import pytest
from tests.eval.runner import EvalRunner


@pytest.mark.asyncio
async def test_e2e_error(load_case, category):
    case_list = load_case(f"{category}/e2e/e2e_error.json")
    cases = case_list if isinstance(case_list, list) else [case_list]
    runner = EvalRunner(case_dirs=[category])

    for case in cases:
        result = await runner.run_case(case)
        errors = []
        for tr in result.trial_results:
            errors.extend(tr.errors)
        assert result.passed and not errors, (
            f"[{result.task_id}] FAILED\n"
            f"  Errors: {'; '.join(errors) if errors else 'none'}"
        )
