from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---- GradeResult ----


@dataclass
class GradeResult:
    passed: bool
    code_results: list[dict] = field(
        default_factory=list
    )  # [{check: str, passed: bool, detail: str}]
    model_results: dict | None = (
        None  # {accuracy/empathy: {score: int, reasoning: str}}
    )
    errors: list[str] = field(default_factory=list)


# ---- CodeGrader ----


class CodeGrader:
    """只检查 state 和结构化数据，不评估回复内容质量。"""

    # ---------- 通用检查 ----------

    def _field_exists(self, state: dict, field: str) -> tuple[bool, str]:
        value = state.get(field)
        if value is None:
            return False, f"field '{field}' is None"
        if isinstance(value, (list, dict, str)) and len(value) == 0:
            return False, f"field '{field}' is empty"
        return True, "ok"

    def _field_equals(self, state: dict, field: str, expected: Any) -> tuple[bool, str]:
        value = state.get(field)
        if value != expected:
            return False, f"field '{field}' expected {expected!r}, got {value!r}"
        return True, "ok"

    def _field_not_equals(
        self, state: dict, field: str, excluded: Any
    ) -> tuple[bool, str]:
        value = state.get(field)
        if value == excluded:
            return False, f"field '{field}' should not be {excluded!r}"
        return True, "ok"

    def _list_contains(
        self, items: list, expected: Any, key: str | None = None
    ) -> tuple[bool, str]:
        if key:
            values = [item.get(key) for item in items if isinstance(item, dict)]
        else:
            values = items
        if expected not in values:
            return False, f"expected {expected!r} not in list ({values[:10]})"
        return True, "ok"

    def _list_not_contains(
        self, items: list, excluded: str, key: str | None = None
    ) -> tuple[bool, str]:
        if key:
            values = [item.get(key) for item in items if isinstance(item, dict)]
        else:
            values = items
        if excluded in values:
            return False, f"unexpected {excluded!r} found in list"
        return True, "ok"

    def _try_process_contains(
        self, steps: list[dict], tool_name: str
    ) -> tuple[bool, str]:
        for step in steps:
            tp = step.get("try_process") or []
            for entry in tp:
                if isinstance(entry, dict) and entry.get("tool") == tool_name:
                    return True, "ok"
        return False, f"tool '{tool_name}' not found in any step's try_process"

    def _try_process_contains_result(
        self, steps: list[dict], tool_name: str
    ) -> tuple[bool, str]:
        """Check try_process has a result entry for tool_name (tool was actually executed)."""
        found_result = False
        for step in steps:
            tp = step.get("try_process") or []
            for entry in tp:
                if (
                    isinstance(entry, dict)
                    and entry.get("tool") == tool_name
                    and "result" in entry
                ):
                    found_result = True
        if not found_result:
            return False, f"tool '{tool_name}' has no result entry in try_process"
        return True, "ok"

    def _slots_have_keys(self, slots: dict | None, keys: list[str]) -> tuple[bool, str]:
        if not slots:
            return False, f"slots is empty, expected keys: {keys}"
        missing = [k for k in keys if k not in slots or not slots[k]]
        if missing:
            return False, f"slots missing keys: {missing}"
        return True, "ok"

    # ---------- Agent 专用检查 ----------

    def grade_router(self, state: dict, expect: dict) -> list[dict]:
        results = []
        if "intent" in expect:
            ok, detail = self._field_equals(state, "intent", expect["intent"])
            results.append({"check": "intent", "passed": ok, "detail": detail})
        return results

    def grade_guard(self, state: dict, expect: dict) -> list[dict]:
        results = []
        if "guard_decision" in expect:
            ok, detail = self._field_equals(
                state, "guard_decision", expect["guard_decision"]
            )
            results.append({"check": "guard_decision", "passed": ok, "detail": detail})
        if expect.get("service_key_not_empty"):
            svc = state.get("service_key")
            ok = bool(svc and isinstance(svc, str) and svc.strip())
            results.append(
                {
                    "check": "service_key_not_empty",
                    "passed": ok,
                    "detail": f"service_key={svc!r}",
                }
            )
        if "service_key" in expect:
            ok, detail = self._field_equals(state, "service_key", expect["service_key"])
            results.append({"check": "service_key", "passed": ok, "detail": detail})
        if expect.get("goal_not_empty"):
            goal = state.get("goal")
            ok = bool(goal and isinstance(goal, str) and goal.strip())
            results.append(
                {"check": "goal_not_empty", "passed": ok, "detail": f"goal={goal!r}"}
            )
        if expect.get("goal_contains"):
            goal = str(state.get("goal") or "")
            for keyword in expect["goal_contains"]:
                ok = keyword.lower() in goal.lower()
                results.append(
                    {
                        "check": f"goal_has:{keyword}",
                        "passed": ok,
                        "detail": f"goal={goal!r}",
                    }
                )
        return results

    def grade_plan(self, state: dict, expect: dict) -> list[dict]:
        results = []
        steps = state.get("steps") or []
        smin = expect.get("steps_min")
        smax = expect.get("steps_max")
        if smin is not None:
            ok = len(steps) >= smin
            results.append(
                {
                    "check": "steps_min",
                    "passed": ok,
                    "detail": f"expected >= {smin}, got {len(steps)}",
                }
            )
        if smax is not None:
            ok = len(steps) <= smax
            results.append(
                {
                    "check": "steps_max",
                    "passed": ok,
                    "detail": f"expected <= {smax}, got {len(steps)}",
                }
            )
        if expect.get("expected_slots_not_empty"):
            expected_slots = state.get("expected_slots") or []
            slot_names = [
                s.get("name") if isinstance(s, dict) else s for s in expected_slots
            ]
            ok = len(slot_names) > 0
            results.append(
                {
                    "check": "expected_slots_not_empty",
                    "passed": ok,
                    "detail": f"slot names: {slot_names}",
                }
            )
        if "expected_slots_contains" in expect:
            expected_slots = state.get("expected_slots") or []
            slot_names = [
                s.get("name") if isinstance(s, dict) else s for s in expected_slots
            ]
            for key in expect["expected_slots_contains"]:
                ok = key in slot_names
                results.append(
                    {
                        "check": f"expected_slot:{key}",
                        "passed": ok,
                        "detail": f"slot names: {slot_names}",
                    }
                )
        if "tools_allowed" in expect:
            allowed = set(expect["tools_allowed"])
            for step in steps:
                for tool_name in step.get("available_tools") or []:
                    if tool_name not in allowed:
                        results.append(
                            {
                                "check": "tools_allowed",
                                "passed": False,
                                "detail": f"step '{step.get('goal', '')}' has invalid tool: {tool_name}",
                            }
                        )
                        break
            else:
                results.append(
                    {
                        "check": "tools_allowed",
                        "passed": True,
                        "detail": f"all tools in allowed set ({len(allowed)} tools)",
                    }
                )
        if "tools_exclude" in expect:
            excluded = set(expect["tools_exclude"])
            for step in steps:
                for tool_name in step.get("available_tools") or []:
                    if tool_name in excluded:
                        results.append(
                            {
                                "check": f"tools_exclude:{tool_name}",
                                "passed": False,
                                "detail": f"step '{step.get('goal', '')}' has excluded tool: {tool_name}",
                            }
                        )
                        break
                else:
                    continue
                break
            else:
                results.append(
                    {
                        "check": "tools_exclude",
                        "passed": True,
                        "detail": f"no excluded tools found ({excluded})",
                    }
                )
        if "final_status" in expect:
            ok, detail = self._field_equals(
                state, "final_status", expect["final_status"]
            )
            results.append({"check": "final_status", "passed": ok, "detail": detail})
        if expect.get("reason_not_empty"):
            reason = str(state.get("reason") or "")
            ok = bool(reason.strip())
            results.append(
                {
                    "check": "reason_not_empty",
                    "passed": ok,
                    "detail": f"reason={reason!r}",
                }
            )
        return results

    def grade_executor(self, state: dict, expect: dict) -> list[dict]:
        results = []
        steps = state.get("steps") or []
        idx = state.get("current_step_index", 0)
        if 0 <= idx < len(steps):
            step = steps[idx]
            if "step_status" in expect:
                ok, detail = self._field_equals(
                    step, "step_status", expect["step_status"]
                )
                results.append({"check": "step_status", "passed": ok, "detail": detail})
            if "try_process_contains" in expect:
                for tool_name in expect["try_process_contains"]:
                    ok, detail = self._try_process_contains([step], tool_name)
                    results.append(
                        {"check": f"tp_has:{tool_name}", "passed": ok, "detail": detail}
                    )
            if "try_process_not_contains" in expect:
                for tool_name in expect["try_process_not_contains"]:
                    ok, detail = self._list_not_contains(
                        [
                            e.get("tool")
                            for e in step.get("try_process") or []
                            if isinstance(e, dict)
                        ],
                        tool_name,
                    )
                    results.append(
                        {"check": f"tp_no:{tool_name}", "passed": ok, "detail": detail}
                    )
        if "slots" in expect:
            if isinstance(expect["slots"], dict):
                for key, expected_val in expect["slots"].items():
                    actual = (state.get("slots") or {}).get(key)
                    ok = actual == expected_val
                    results.append(
                        {
                            "check": f"slot:{key}",
                            "passed": ok,
                            "detail": f"expected {expected_val!r}, got {actual!r}",
                        }
                    )
        if "slots_have_keys" in expect:
            slots = state.get("slots") or {}
            for key in expect["slots_have_keys"]:
                ok = key in slots and bool(slots[key])
                results.append(
                    {
                        "check": f"slot:{key}",
                        "passed": ok,
                        "detail": f"value={slots.get(key)!r}",
                    }
                )
        return results

    def grade_qa(self, state: dict, expect: dict) -> list[dict]:
        results = []
        reply = state.get("final_reply") or ""
        ok = bool(reply and len(reply.strip()) > 0)
        results.append(
            {
                "check": "final_reply_not_empty",
                "passed": ok,
                "detail": f"final_reply length={len(reply)}",
            }
        )
        if expect.get("final_reply_not_fallback"):
            is_fallback = "暂时无法" in reply or "请稍后再试" in reply
            results.append(
                {
                    "check": "not_fallback",
                    "passed": not is_fallback,
                    "detail": f"reply={reply[:100]}",
                }
            )

        # 工具调用 trace 检查（从 QAAgent._last_result_messages 提取）
        tool_trace = state.get("_tool_trace") or []
        tool_names = [t["tool"] for t in tool_trace]
        if "tool_trace_contains" in expect:
            for tool_name in expect["tool_trace_contains"]:
                ok = tool_name in tool_names
                results.append(
                    {
                        "check": f"tool:{tool_name}",
                        "passed": ok,
                        "detail": f"tools called: {tool_names}",
                    }
                )
        if "tool_trace_not_contains" in expect:
            for tool_name in expect["tool_trace_not_contains"]:
                ok = tool_name not in tool_names
                results.append(
                    {
                        "check": f"no:{tool_name}",
                        "passed": ok,
                        "detail": f"tools called: {tool_names}",
                    }
                )
        if "tool_trace_content_contains" in expect:
            for item in expect["tool_trace_content_contains"]:
                tool_name = item.get("tool") if isinstance(item, dict) else item
                keyword = item.get("text") if isinstance(item, dict) else ""
                found = False
                detail = ""
                for t in tool_trace:
                    if (
                        t["tool"] == tool_name
                        and keyword.lower() in t["content"].lower()
                    ):
                        found = True
                        detail = t["content"][:200]
                        break
                if not detail:
                    detail = "not found"
                results.append(
                    {
                        "check": f"rag_content:{tool_name}:{keyword[:30]}",
                        "passed": found,
                        "detail": detail,
                    }
                )
        return results

    def grade_recommend_guard(self, state: dict, expect: dict) -> list[dict]:
        results = []
        if "task_completed" in expect:
            ok, detail = self._field_equals(
                state, "task_completed", expect["task_completed"]
            )
            results.append({"check": "task_completed", "passed": ok, "detail": detail})
        if expect.get("summary_not_empty"):
            s = str(state.get("summary") or "")
            ok = bool(s.strip())
            results.append(
                {
                    "check": "summary_not_empty",
                    "passed": ok,
                    "detail": f"summary={s[:80]}",
                }
            )
        if expect.get("anchor_products_not_empty"):
            prods = state.get("anchor_products") or []
            ok = isinstance(prods, list) and len(prods) > 0
            results.append(
                {
                    "check": "anchor_products_not_empty",
                    "passed": ok,
                    "detail": f"count={len(prods) if isinstance(prods, list) else 0}",
                }
            )
        if "anchor_products_have_field" in expect:
            prods = state.get("anchor_products") or []
            field = expect["anchor_products_have_field"]
            ok = (
                all(isinstance(p, dict) and field in p for p in prods)
                if prods
                else True
            )
            results.append(
                {
                    "check": f"anchor_has:{field}",
                    "passed": ok,
                    "detail": f"count={len(prods)}",
                }
            )
        if "current_subgraph" in expect:
            ok, detail = self._field_equals(
                state, "current_subgraph", expect["current_subgraph"]
            )
            results.append(
                {"check": "current_subgraph", "passed": ok, "detail": detail}
            )
        return results

    def grade_recommend(self, state: dict, expect: dict) -> list[dict]:
        results = []
        if "final_status" in expect:
            ok, detail = self._field_equals(
                state, "final_status", expect["final_status"]
            )
            results.append({"check": "final_status", "passed": ok, "detail": detail})
        if "current_subgraph" in expect:
            ok, detail = self._field_equals(
                state, "current_subgraph", expect["current_subgraph"]
            )
            results.append(
                {"check": "current_subgraph", "passed": ok, "detail": detail}
            )
        if expect.get("products_not_empty"):
            prods = state.get("products") or []
            ok = isinstance(prods, list) and len(prods) > 0
            results.append(
                {
                    "check": "products_not_empty",
                    "passed": ok,
                    "detail": f"count={len(prods)}",
                }
            )
        if expect.get("final_reply_not_empty"):
            ok, detail = self._field_exists(state, "final_reply")
            results.append(
                {"check": "final_reply_not_empty", "passed": ok, "detail": detail}
            )

        # 工具调用 trace（从 _result_messages）
        tool_trace = state.get("_tool_trace") or []
        tool_names = [t["tool"] for t in tool_trace]
        if "tool_trace_contains" in expect:
            for tool_name in expect["tool_trace_contains"]:
                ok = tool_name in tool_names
                results.append(
                    {
                        "check": f"tool:{tool_name}",
                        "passed": ok,
                        "detail": f"tools: {tool_names}",
                    }
                )
        if expect.get("tool_trace_count_max"):
            max_count = expect["tool_trace_count_max"]
            ok = len(tool_trace) <= max_count
            results.append(
                {
                    "check": "tool_trace_count_max",
                    "passed": ok,
                    "detail": f"count={len(tool_trace)} <= {max_count}",
                }
            )
        return results

    def grade_post_process(self, result: dict, expect: dict) -> list[dict]:
        results = []
        if "format_valid" in expect:
            ok = bool(result.get("summary") or result.get("facts"))
            results.append(
                {
                    "check": "format_valid",
                    "passed": ok,
                    "detail": "summary or facts exist",
                }
            )
        return results

    def grade_user_facts(self, state: dict, expect: dict) -> list[dict]:
        results = []
        facts = [str(f).casefold() for f in (state.get("facts") or [])]

        if "facts_contains" in expect:
            for keyword in expect["facts_contains"]:
                ok = any(keyword.lower() in f for f in facts)
                results.append(
                    {
                        "check": f"fact_has:{keyword}",
                        "passed": ok,
                        "detail": f"facts={facts}",
                    }
                )
        if "facts_not_contains" in expect:
            for keyword in expect["facts_not_contains"]:
                ok = not any(keyword.lower() in f for f in facts)
                results.append(
                    {
                        "check": f"fact_no:{keyword}",
                        "passed": ok,
                        "detail": f"facts={facts}",
                    }
                )
        return results

    def grade_e2e(self, state: dict, expect: dict) -> list[dict]:
        """E2E CodeGrader 检查。expect 来自 case JSON。"""
        results = []
        if "intent" in expect:
            ok, detail = self._field_equals(state, "intent", expect["intent"])
            results.append({"check": "intent", "passed": ok, "detail": detail})
        if "final_status" in expect:
            ok, detail = self._field_equals(
                state, "final_status", expect["final_status"]
            )
            results.append({"check": "final_status", "passed": ok, "detail": detail})
        if "try_process_contains" in expect:
            steps = state.get("steps") or []
            for tool_name in expect["try_process_contains"]:
                ok, detail = self._try_process_contains(steps, tool_name)
                results.append(
                    {"check": f"tp_has:{tool_name}", "passed": ok, "detail": detail}
                )
        if "try_process_contains_result" in expect:
            steps = state.get("steps") or []
            for tool_name in expect["try_process_contains_result"]:
                ok, detail = self._try_process_contains_result(steps, tool_name)
                results.append(
                    {
                        "check": f"tp_has_result:{tool_name}",
                        "passed": ok,
                        "detail": detail,
                    }
                )
        if "not_500" in expect:
            ok = state.get("_http_status") != 500
            results.append(
                {
                    "check": "not_500",
                    "passed": ok,
                    "detail": f"status={state.get('_http_status')}",
                }
            )
        if expect.get("graceful_degradation"):
            reply = state.get("final_reply") or ""
            ok = bool(reply.strip())
            results.append(
                {
                    "check": "graceful_degradation",
                    "passed": ok,
                    "detail": "has non-empty reply",
                }
            )
        if "current_subgraph" in expect:
            ok, detail = self._field_equals(
                state, "current_subgraph", expect["current_subgraph"]
            )
            results.append(
                {"check": "current_subgraph", "passed": ok, "detail": detail}
            )
        if expect.get("final_reply_not_empty"):
            ok, detail = self._field_exists(state, "final_reply")
            results.append(
                {"check": "final_reply_not_empty", "passed": ok, "detail": detail}
            )
        return results


# ---- ModelGrader ----


class ModelGrader:
    """用 LLM (judge role) 评估回复质量：accuracy, completeness, empathy。"""

    def __init__(self):
        self._llm = None
        self._prompt = None

    def _get_llm(self):
        if self._llm is None:
            from app.llm.llm_factory import get_llm

            self._llm = get_llm("judge")
        return self._llm

    def _get_prompt(self) -> str:
        if self._prompt is None:
            from pathlib import Path

            prompt_path = Path(__file__).parent / "prompts" / "model_grader.txt"
            self._prompt = prompt_path.read_text(encoding="utf-8")
        return self._prompt

    async def grade(
        self,
        rubric: list[str],
        transcript: list[dict],
        scenario: str = "",
        reference: str = "",
    ) -> dict:
        """对一段对话进行评分。

        Args:
            rubric: 需要评分的维度列表，如 ["accuracy", "completeness", "empathy"]
            transcript: 对话记录 [{"role": "user/assistant", "content": "..."}, ...]
            scenario: 场景描述（可选）

        Returns:
            dict with keys matching rubric values, each: {"score": int 0-5, "reasoning": str}
        """
        if not rubric:
            return {}

        prompt = self._get_prompt()
        transcript_str = "\n".join(f"[{t['role']}]: {t['content']}" for t in transcript)

        ref_section = f"## 参考答案 / 标准\n{reference}" if reference else ""
        user_prompt = f"""## 场景描述
{scenario or "(无)"}

{ref_section}

## 需要评估的维度
{", ".join(rubric)}

## 对话记录
{transcript_str}

请评估以上对话，对每个维度给出 0-5 分和理由。"""

        llm = self._get_llm()
        response = await llm.ainvoke(
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

        return self._parse_response(str(response.content), rubric)

    def _parse_response(self, content: str, rubric: list[str]) -> dict:
        """解析 LLM 返回的评分文本为结构化结果。"""
        result = {}
        try:
            import json

            # Try to extract a JSON block
            import re

            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                data = json.loads(match.group())
                for dim in rubric:
                    dim_data = data.get(dim, {})
                    result[dim] = {
                        "score": int(dim_data.get("score", -1)),
                        "reasoning": str(dim_data.get("reasoning", "")),
                    }
                return result
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback: return raw content as reasoning for all dimensions
        for dim in rubric:
            result[dim] = {"score": -1, "reasoning": content[:500]}
        return result
