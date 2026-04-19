from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any, Dict, List

import httpx
from app.config.config import settings


_SCRM_ORIGIN = "http://scrm:3658"
_API_ORIGIN = "http://127.0.0.1:8000"
_TEST_SUB = "api_ticket_probe"
_HTTP_TIMEOUT = 180.0


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


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _build_access_token(sub: str = _TEST_SUB) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": sub,
        "iss": settings.jwt_issuer,
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}"
    digest = hmac.new(
        settings.jwt_secret_key.encode("utf-8"),
        signing_input.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = _b64url_encode(digest)
    return f"{signing_input}.{signature}"


def _run_thread(thread_id: str, messages: List[str]) -> Dict[str, Any]:
    token = _build_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    turns: List[Dict[str, Any]] = []
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        for turn_index, message in enumerate(messages, start=1):
            started = time.perf_counter()
            try:
                response = client.post(
                    f"{_API_ORIGIN}/api/v1/chat",
                    json={"message": message, "thread_id": thread_id, "channel": "api"},
                    headers=headers,
                )
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                try:
                    payload = response.json()
                except Exception:
                    payload = {"raw": response.text}
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
                    "state": {},
                }
            )
    return {
        "thread_id": thread_id,
        "turns": turns,
        "final_state": {},
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


def _final_state(case_result: Dict[str, Any]) -> Dict[str, Any]:
    return dict(case_result.get("final_state") or {})


def _evaluate_case(case_name: str, case_result: Dict[str, Any]) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []
    first_reply = _reply_text(case_result, 0)
    final_reply = _reply_text(case_result, -1)

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append({"name": name, "passed": passed, "detail": detail})

    check("http_status", _all_status_ok(case_result), "all turns should return 200")

    if case_name == "quality_first_turn":
        check(
            "quality_route_alive",
            bool(first_reply),
            first_reply,
        )
        check(
            "asks_or_interacts",
            _contains_any(first_reply, ["订单号", "购买", "订单信息"])
            or _interaction_type(case_result, 0) in {"select_order", "select_product"},
            first_reply,
        )
    elif case_name == "quality_two_turns":
        check(
            "turn1_route_alive",
            bool(first_reply),
            first_reply,
        )
        check(
            "turn2_not_crash",
            bool(final_reply),
            final_reply,
        )
    elif case_name == "explicit_refund_first_turn":
        check(
            "asks_order_or_selects_order",
            _contains_any(first_reply, ["订单号", "购买", "下单"])
            or _interaction_type(case_result, 0) in {"select_order", "select_product"},
            first_reply,
        )
        if _interaction_type(case_result, 0) in {"select_order", "select_product"}:
            check("select_order_contract", _interaction_contract_ok(case_result, 0), final_reply)
    elif case_name == "explicit_change_first_turn":
        check(
            "asks_or_interacts",
            _contains_any(first_reply, ["订单号", "商品", "购买"])
            or _interaction_type(case_result, 0) in {"select_order", "select_product"},
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
            "turn1_interacts_or_clarifies",
            bool(first_reply) and (
                _contains_any(first_reply, ["工单号", "订单", "售后"])
                or _interaction_type(case_result, 0) == "select_ticket"
            ),
            first_reply,
        )
        check(
            "final_turn_alive",
            bool(final_reply),
            final_reply,
        )
    elif case_name == "query_clarified_two_turns":
        check(
            "turn1_query_alive",
            bool(first_reply),
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
    elif case_name == "cancel_midway":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "cancel_reply_alive",
            bool(final_reply) and _contains_any(final_reply, ["取消", "不处理", "结束"]),
            final_reply,
        )
    elif case_name == "vague_ticket_clarify":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check("clarifies", bool(first_reply), first_reply)
    elif case_name == "direct_ticket_id_query":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "returns_ticket_progress",
            _contains_any(final_reply, ["工单", "处理中", "待处理", "状态", "进度"]),
            final_reply,
        )
    elif case_name == "offline_service_complain":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check("complain_alive", bool(first_reply), first_reply)
    elif case_name == "points_not_arrived":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check("equity_alive", bool(first_reply), first_reply)
    elif case_name == "mixed_intent_route_alive":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "mixed_intent_not_recommend",
            not _contains_any(final_reply, ["帮您推荐", "推荐", "更合适", "搭配"]),
            final_reply,
        )
    elif case_name == "query_select_ticket_success":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "returns_progress_after_selection",
            _contains_any(final_reply, ["工单", "处理中", "待处理", "状态", "进度"]),
            final_reply,
        )
    elif case_name == "query_select_ticket_free_text_no_crash":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "free_text_reply_alive",
            bool(final_reply) or _interaction_contract_ok(case_result, -1),
            final_reply,
        )
    elif case_name == "refund_select_order_success":
        check("status_ok", _all_status_ok(case_result), "all turns should return 200")
        check(
            "returns_refund_path",
            bool(final_reply)
            and (
                _interaction_type(case_result, -1) in {"select_product", "confirm_ticket"}
                or _contains_any(final_reply, ["退货", "换货", "售后", "工单"])
            ),
            final_reply,
        )

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
            "name": "query_select_ticket_success",
            "messages": [
                "帮我查一下我的售后工单进度",
                "TK202604150001",
            ],
        },
        {
            "name": "query_select_ticket_free_text_no_crash",
            "messages": [
                "帮我查一下我的售后工单进度",
                "我先不选，你直接说下大概情况",
            ],
        },
        {
            "name": "refund_select_order_success",
            "messages": [
                "这个东西坏了，我要退货",
                "N20260305000012",
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
        {
            "name": "cancel_midway",
            "messages": [
                "这个东西坏了，我要退货",
                "算了，不处理了",
            ],
        },
    ]


def _edge_cases() -> List[Dict[str, Any]]:
    return [
        {
            "name": "vague_ticket_clarify",
            "messages": ["就那个售后，你们懂的"],
        },
        {
            "name": "direct_ticket_id_query",
            "messages": ["帮我查工单 TK202604150001 的进度"],
        },
        {
            "name": "offline_service_complain",
            "messages": ["我要投诉门店服务态度差"],
        },
        {
            "name": "points_not_arrived",
            "messages": ["积分为什么没到账"],
        },
        {
            "name": "mixed_intent_route_alive",
            "messages": ["我先问一下会员升级，再帮我查售后工单"],
        },
    ]


def _run_cases(mode: str, cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    report: Dict[str, Any] = {
        "mode": mode,
        "probe": _probe_scrm(f"container {mode} probe"),
        "cases": [],
    }

    timestamp = int(time.time())
    for index, case in enumerate(cases, start=1):
        thread_id = f"ticket_reg_standard_{timestamp}_{index}"
        case_result = _run_thread(thread_id, case["messages"])
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


def run_suite() -> Dict[str, Any]:
    return _run_cases("standard", _suite_cases())


def run_edge_suite() -> Dict[str, Any]:
    return _run_cases("edge", _edge_cases())


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
    results["modes"].append(run_edge_suite())

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
