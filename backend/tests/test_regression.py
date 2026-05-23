"""CI 回归门禁 — merge main 时跑 regression/ 下 E2E case。

当前 regression/e2e/ 尚无 E2E（均在 capability/e2e/），毕业后逐个移入，CI 自动纳入。

用法: docker compose exec backend python -m pytest tests/test_regression.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.eval.runner import EvalRunner


def _load_regression_e2e_cases() -> list[dict]:
    """加载 regression/ 下所有 JSON case。只跑 E2E（scenario + turns），跳过 Agent 单测。"""
    cases_dir = Path(__file__).parent / "eval" / "cases" / "regression"
    all_cases: list[dict] = []
    for json_file in sorted(cases_dir.glob("e2e/*.json")):
        data = json.loads(json_file.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        for case in items:
            case["_source"] = json_file.name
        all_cases.extend(items)
    return all_cases


@pytest.mark.skipif(
    not _load_regression_e2e_cases(),
    reason="regression/ 下暂无 E2E case",
)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    _load_regression_e2e_cases(),
    ids=lambda c: c.get("task_id", "?"),
)
async def test_regression_case(case: dict):
    runner = EvalRunner(case_dirs=["regression"])
    result = await runner.run_case(case)

    fails: list[str] = []
    for tr in result.trial_results:
        for r in tr.code_results:
            if not r["passed"]:
                fails.append(f"{r['check']}: {r['detail']}")
        for e in tr.errors:
            fails.append(f"error: {e}")
        if tr.model_results:
            for dim, d in tr.model_results.items():
                if d.get("score", 0) < 2:
                    fails.append(f"model:{dim}={d.get('score')}")

    assert result.passed, (
        f"[{result.task_id}] FAILED ({result.passed_trials}/{result.trials}):\n  "
        + "\n  ".join(fails)
        if fails
        else "unknown"
    )
