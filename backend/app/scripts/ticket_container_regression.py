from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List

import httpx
from fastapi.testclient import TestClient

import app.workflow.graph as graph_module
from app.main import app
from app.agents.llm.llm_factory import get_local_llm, get_remote_llm
from app.security.jwt_auth import AuthContext, JWTPayload, get_auth_context


_SCRM_ORIGIN = "http://scrm:3658"


def _fake_auth() -> AuthContext:
    return AuthContext(
        access_token="test-token",
        claims=JWTPayload(sub="api_ticket_probe", exp=9_999_999_999),
    )


@contextmanager
def _override_auth() -> Any:
    app.dependency_overrides[get_auth_context] = _fake_auth
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_auth_context, None)


def _probe_scrm(note: str) -> Dict[str, Any]:
    url = f"{_SCRM_ORIGIN}/order"
    started = time.perf_counter()
    try:
        response = httpx.get(
            url,
            params={"user_id": "api_ticket_probe", "page": 1, "page_size": 5},
            headers={"Authorization": "Bearer test-token"},
            timeout=10.0,
        )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "note": note,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "body_preview": response.text[:500],
            "ok": response.is_success,
        }
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "note": note,
            "status_code": None,
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
            "ok": False,
        }


def _serialize_steps(raw_steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    serialized: List[Dict[str, Any]] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        serialized.append(
            {
                "id": step.get("id"),
                "goal": step.get("goal") or step.get("purpose"),
                "completion_signal": step.get("completion_signal"),
                "target_slots": step.get("target_slots") or [],
                "available_tools": step.get("available_tools") or [],
                "is_success": step.get("is_success"),
                "result_keys": sorted(list((step.get("result") or {}).keys()))
                if isinstance(step.get("result"), dict)
                else [],
            }
        )
    return serialized


def _load_thread_state(thread_id: str) -> Dict[str, Any]:
    workflow = graph_module.get_workflow()
    saved_state = workflow.get_state({"configurable": {"thread_id": thread_id}})
    values = dict(getattr(saved_state, "values", {}) or {})
    return {
        "intent": values.get("intent"),
        "ticket_scene": values.get("ticket_scene"),
        "current_goal": values.get("current_goal"),
        "expected_slots": values.get("expected_slots") or [],
        "slots": values.get("slots") or {},
        "current_step_index": values.get("current_step_index"),
        "next_action": str(values.get("next_action") or ""),
        "final_status": values.get("final_status"),
        "final_reason": values.get("final_reason"),
        "steps": _serialize_steps(list(values.get("steps") or [])),
    }


def _run_thread(client: TestClient, thread_id: str, messages: List[str]) -> Dict[str, Any]:
    turns: List[Dict[str, Any]] = []
    for turn_index, message in enumerate(messages, start=1):
        started = time.perf_counter()
        try:
            response = client.post(
                "/api/v1/chat",
                json={"message": message, "thread_id": thread_id, "channel": "api"},
            )
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            payload = response.json()
            state_snapshot = _load_thread_state(thread_id)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            turns.append(
                {
                    "turn": turn_index,
                    "request": message,
                    "status_code": None,
                    "elapsed_ms": elapsed_ms,
                    "response": {"error": str(exc)},
                    "state": {},
                }
            )
            break
        turns.append(
            {
                "turn": turn_index,
                "request": message,
                "status_code": response.status_code,
                "elapsed_ms": elapsed_ms,
                "response": payload,
                "state": state_snapshot,
            }
        )
    return {
        "thread_id": thread_id,
        "turns": turns,
        "final_state": turns[-1]["state"] if turns else {},
    }


def _reply_text(case_result: Dict[str, Any], turn: int = -1) -> str:
    turns = list(case_result.get("turns") or [])
    if not turns:
        return ""
    payload = turns[turn].get("response") or {}
    return str(payload.get("reply") or "").strip()


def _interaction_type(case_result: Dict[str, Any], turn: int = -1) -> str:
    turns = list(case_result.get("turns") or [])
    if not turns:
        return ""
    payload = turns[turn].get("response") or {}
    interaction = payload.get("interaction") or {}
    return str(interaction.get("interaction_type") or "").strip()


def _interaction_contract_ok(case_result: Dict[str, Any], turn: int = -1) -> bool:
    turns = list(case_result.get("turns") or [])
    if not turns:
        return False
    payload = turns[turn].get("response") or {}
    interaction = payload.get("interaction")
    if not isinstance(interaction, dict):
        return False
    if not str(interaction.get("interaction_type") or "").strip():
        return False
    items = interaction.get("items")
    if not isinstance(items, list) or not items:
        return False
    for item in items:
        if not isinstance(item, dict):
            return False
        if not str(item.get("key") or "").strip():
            return False
        if not str(item.get("label") or "").strip():
            return False
        if not isinstance(item.get("detail"), dict):
            return False
    return True


def _all_status_ok(case_result: Dict[str, Any]) -> bool:
    turns = list(case_result.get("turns") or [])
    return bool(turns) and all(turn.get("status_code") == 200 for turn in turns)


def _contains_any(text: str, keywords: List[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _evaluate_case(case_name: str, case_result: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    first_reply = _reply_text(case_result, 0)
    final_reply = _reply_text(case_result, -1)

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    check("http_status", _all_status_ok(case_result), "all turns should return 200")

    if case_name == "quality_first_turn":
        check(
            "asks_order_info",
            _contains_any(first_reply, ["订单号", "购买", "订单信息"])
            or _interaction_type(case_result, 0) == "select_order",
            first_reply,
        )
    elif case_name == "quality_two_turns":
        check(
            "turn1_asks_order_info",
            _contains_any(first_reply, ["订单号", "购买", "订单信息"])
            or _interaction_type(case_result, 0) == "select_order",
            first_reply,
        )
        check(
            "turn2_not_crash",
            bool(final_reply),
            final_reply,
        )
    elif case_name == "explicit_refund_first_turn":
        check(
            "refund_route_no_quality_wording",
            "质量问题" not in first_reply,
            first_reply,
        )
        check(
            "asks_order_or_selects_order",
            _contains_any(first_reply, ["订单号", "购买", "下单"])
            or _interaction_type(case_result, 0) == "select_order",
            first_reply,
        )
        if _interaction_type(case_result, 0) == "select_order":
            check("select_order_contract", _interaction_contract_ok(case_result, 0), final_reply)
    elif case_name == "explicit_change_first_turn":
        check(
            "asks_order_or_product",
            _contains_any(first_reply, ["订单号", "商品", "购买"]),
            first_reply,
        )
    elif case_name == "complain_first_turn":
        check(
            "complain_route_alive",
            bool(first_reply),
            first_reply,
        )
    elif case_name == "equity_first_turn":
        check(
            "equity_route_alive",
            bool(first_reply),
            first_reply,
        )
    elif case_name == "query_ambiguous_three_turns":
        check(
            "turn1_clarifies_scene",
            _contains_any(first_reply, ["哪类", "退货", "换货", "工单号"]),
            first_reply,
        )
        check(
            "final_turn_finishes",
            not _contains_any(final_reply, ["请问您", "请直接回复", "哪类工单"]),
            final_reply,
        )
    elif case_name == "query_clarified_two_turns":
        check(
            "turn1_clarifies_scene",
            _contains_any(first_reply, ["哪类", "工单号", "退货", "换货"]),
            first_reply,
        )
        check(
            "turn2_progress_path_alive",
            bool(final_reply),
            final_reply,
        )
    elif case_name == "recommend_non_ticket":
        check(
            "recommend_route",
            _contains_any(first_reply, ["推荐", "帮您看看", "更合适"]),
            first_reply,
        )
    elif case_name == "qa_non_ticket":
        check(
            "qa_route",
            _contains_any(first_reply, ["政策", "规则", "帮您查", "说明"]),
            first_reply,
        )
    elif case_name == "quality_select_product_two_turns":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "select_product_interaction",
            _interaction_type(case_result, -1) == "select_product",
            final_reply,
        )
        check(
            "select_product_contract",
            _interaction_contract_ok(case_result, -1),
            final_reply,
        )
    elif case_name == "query_select_ticket_two_turns":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "select_ticket_interaction",
            _interaction_type(case_result, -1) == "select_ticket",
            final_reply,
        )
        check(
            "select_ticket_contract",
            _interaction_contract_ok(case_result, -1),
            final_reply,
        )
    elif case_name in {"http_error_quality_first_turn", "inconsistent_quality_two_turns", "empty_query_two_turns"}:
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check("route_alive", bool(final_reply), final_reply)

    passed = all(item["passed"] for item in checks)
    return {"passed": passed, "checks": checks}


def _suite_cases() -> List[Dict[str, Any]]:
    return [
        {
            "name": "quality_first_turn",
            "messages": ["我买的电饭煲有质量问题，内胆破损，想申请售后处理。"],
        },
        {
            "name": "quality_two_turns",
            "messages": [
                "我买的电饭煲有质量问题，内胆破损，想申请售后处理。",
                "订单号是N20260305000012",
            ],
        },
        {
            "name": "explicit_refund_first_turn",
            "messages": ["这个东西坏了，我要退货"],
        },
        {
            "name": "explicit_change_first_turn",
            "messages": ["我想把这双鞋换个尺码"],
        },
        {
            "name": "complain_first_turn",
            "messages": ["我要投诉你们售后处理太慢了"],
        },
        {
            "name": "equity_first_turn",
            "messages": ["我的会员权益没有到账，想申请处理。"],
        },
        {
            "name": "query_ambiguous_three_turns",
            "messages": [
                "帮我查一下我的售后工单进度",
                "我就是想查工单",
                "就是售后那个",
            ],
        },
        {
            "name": "query_clarified_two_turns",
            "messages": [
                "帮我查一下我的售后工单进度",
                "质量问题",
            ],
        },
        {
            "name": "quality_select_product_two_turns",
            "messages": [
                "我买的电饭煲有质量问题，内胆破损，想申请售后处理。",
                "订单号是N20260305000012",
            ],
        },
        {
            "name": "query_select_ticket_two_turns",
            "messages": [
                "帮我查一下我的售后工单进度",
                "质量问题",
            ],
        },
        {
            "name": "recommend_non_ticket",
            "messages": ["帮我推荐一双适合春天穿的鞋子"],
        },
        {
            "name": "qa_non_ticket",
            "messages": ["会员升级条件是什么"],
        },
    ]


def run_suite() -> Dict[str, Any]:
    cases = _suite_cases()
    report: Dict[str, Any] = {
        "mode": "standard",
        "probe": _probe_scrm("container standard probe"),
        "cases": [],
    }

    with _override_auth():
        timestamp = int(time.time())
        for index, case in enumerate(cases, start=1):
            get_local_llm.cache_clear()
            get_remote_llm.cache_clear()
            graph_module.workflow = None
            with TestClient(app) as client:
                thread_id = f"ticket_reg_standard_{timestamp}_{index}"
                case_result = _run_thread(client, thread_id, case["messages"])
            report["cases"].append(
                {
                    "name": case["name"],
                    **case_result,
                    "evaluation": _evaluate_case(case["name"], case_result),
                }
            )
    passed_cases = [case for case in report["cases"] if bool((case.get("evaluation") or {}).get("passed"))]
    report["summary"] = {
        "case_count": len(report["cases"]),
        "passed_case_count": len(passed_cases),
        "failed_case_count": len(report["cases"]) - len(passed_cases),
    }
    first_turn_elapsed = [
        float(case["turns"][0]["elapsed_ms"])
        for case in report["cases"]
        if case.get("turns")
    ]
    total_elapsed = [
        float(sum(float(turn.get("elapsed_ms") or 0) for turn in case.get("turns") or []))
        for case in report["cases"]
    ]
    report["timing"] = {
        "first_turn_avg_ms": round(sum(first_turn_elapsed) / len(first_turn_elapsed), 2) if first_turn_elapsed else 0.0,
        "total_case_avg_ms": round(sum(total_elapsed) / len(total_elapsed), 2) if total_elapsed else 0.0,
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run ticket regression inside container")
    parser.add_argument(
        "--output",
        default="/app/docs/ticket_container_regression_results.json",
        help="Output JSON path inside container",
    )
    args = parser.parse_args()

    results: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "modes": [],
    }

    results["modes"].append(run_suite())

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
