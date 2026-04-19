from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from app.config.config import settings
from app.config.redis import get_redis_client
from app.config.redis_keys import SERVICE_MEMORY_SCAN_PATTERN
from app.agents.memory.service_memory import load_last_service_memory, load_recent_service_memories, load_service_messages
from app.workflow.state import AgentState


API_BASE_URL = "http://127.0.0.1:8000"
CHAT_PATH = "/api/v1/chat"
CHANNEL = "api"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _sign(signing_input: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), signing_input.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def build_token(user_id: str, ttl_seconds: int = 3600) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "iss": settings.jwt_issuer,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(f"{header_b64}.{payload_b64}", settings.jwt_secret_key)
    return f"{header_b64}.{payload_b64}.{signature}"


async def clear_user_memory(user_id: str) -> None:
    redis = await get_redis_client()
    cursor = 0
    pattern = SERVICE_MEMORY_SCAN_PATTERN.format(user_id=user_id)
    keys: List[str] = []
    while True:
        cursor, batch = await redis.scan(cursor=cursor, match=pattern, count=100)
        keys.extend(batch)
        if cursor == 0:
            break
    if keys:
        await redis.delete(*keys)


async def post_chat(
    client: httpx.AsyncClient,
    *,
    user_id: str,
    message: str,
    thread_id: str,
) -> Dict[str, Any]:
    token = build_token(user_id)
    response = await client.post(
        CHAT_PATH,
        headers={"Authorization": f"Bearer {token}"},
        json={
            "message": message,
            "thread_id": thread_id,
            "channel": CHANNEL,
        },
        timeout=180.0,
    )
    response.raise_for_status()
    return response.json()


async def inspect_user_memory(user_id: str, thread_id: str) -> Dict[str, Any]:
    recent = await load_recent_service_memories(user_id, thread_id)
    recent_service_items = [item for item in recent if item.get("type") != "archive_summary"]
    archive_items = [item for item in recent if item.get("type") == "archive_summary"]
    last_service = await load_last_service_memory(user_id, thread_id)
    archived_messages: List[Dict[str, Any]] = []
    if last_service and last_service.get("messages_ref"):
        archived_messages = await load_service_messages(str(last_service["messages_ref"]))
    return {
        "recent": recent_service_items,
        "archive_summary": archive_items[0]["content"] if archive_items else "",
        "last_service": last_service,
        "archived_messages": archived_messages,
    }


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected!r}, actual={actual!r}")


def first_interaction_key(response: Dict[str, Any]) -> str:
    interaction = response.get("interaction") or {}
    items = interaction.get("items") or []
    if not items:
        raise AssertionError("expected interaction items but got none")
    return str(items[0].get("key") or "").strip()


def trace_len(last_service: Dict[str, Any] | None) -> int:
    if not isinstance(last_service, dict):
        return 0
    trace = last_service.get("trace") or []
    if isinstance(trace, str):
        return 1 if trace.strip() else 0
    if isinstance(trace, list):
        return len([item for item in trace if str(item).strip()])
    return 0


def service_key(last_service: Dict[str, Any] | None) -> str:
    if not isinstance(last_service, dict):
        return ""
    return str((last_service.get("facts") or {}).get("service_key") or "").strip()


def service_id(service: Dict[str, Any] | None) -> str:
    if not isinstance(service, dict):
        return ""
    return str(service.get("service_id") or "").strip()


def recent_service_ids(memory: Dict[str, Any]) -> List[str]:
    items = memory.get("recent") or []
    return [sid for sid in (service_id(item) for item in items) if sid]


def latest_recent_service(memory: Dict[str, Any]) -> Dict[str, Any] | None:
    items = memory.get("recent") or []
    if not items:
        return None
    last_item = items[-1]
    return last_item if isinstance(last_item, dict) else None


async def post_and_inspect(
    client: httpx.AsyncClient,
    *,
    user_id: str,
    thread_id: str,
    message: str,
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    response = await post_chat(client, user_id=user_id, message=message, thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    return response, memory


@dataclass
class CaseResult:
    name: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


CaseFunc = Callable[[httpx.AsyncClient], Awaitable[CaseResult]]


async def case_01_qa_basic(client: httpx.AsyncClient) -> CaseResult:
    name = "01.qa_basic"
    user_id = "case01_qa_basic"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="会员升级都有哪些条件？", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(len(memory["recent"]), 1, "qa basic should create one service memory")
    assert_equal(last.get("intent"), "qa", "qa basic should end as qa")
    assert_true(trace_len(last) >= 1, "qa basic should have trace")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_02_qa_simple_ack(client: httpx.AsyncClient) -> CaseResult:
    name = "02.qa_simple_ack"
    user_id = "case02_qa_ack"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="会员升级都有哪些条件？", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="好的，谢谢", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before ack should have one service memory")
    assert_equal(len(after["recent"]), 1, "simple ack should not create new service memory")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"]})


async def case_03_recommend_basic(client: httpx.AsyncClient) -> CaseResult:
    name = "03.recommend_basic"
    user_id = "case03_recommend_basic"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="给我推荐一款适合送人的小家电", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(len(memory["recent"]), 1, "recommend basic should create one service memory")
    assert_equal(last.get("intent"), "recommend", "should classify as recommend")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_04_recommend_simple_ack(client: httpx.AsyncClient) -> CaseResult:
    name = "04.recommend_simple_ack"
    user_id = "case04_recommend_ack"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="给我推荐一款适合送人的小家电", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="先这样吧，谢谢", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before ack should have one service memory")
    assert_equal(len(after["recent"]), 1, "simple ack should not create new recommend memory")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"]})


async def case_05_search_ticket_basic(client: httpx.AsyncClient) -> CaseResult:
    name = "05.search_ticket_basic"
    user_id = "case05_search_basic"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(len(memory["recent"]), 1, "search ticket should create one service memory")
    assert_equal(last.get("intent"), "ticket", "search ticket should stay in ticket")
    assert_equal(service_key(last), "search-ticket", "service should be search-ticket")
    assert_true(trace_len(last) >= 2, "ticket should produce trace")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_06_search_ticket_followup_merge(client: httpx.AsyncClient) -> CaseResult:
    name = "06.search_ticket_followup_merge"
    user_id = "case06_search_merge"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="预计什么时候能处理完？", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before followup should have one memory")
    assert_equal(len(after["recent"]), 1, "same-service followup should merge into last service memory")
    assert_equal(service_key(after["last_service"]), "search-ticket", "merged memory should stay on search-ticket")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": after["last_service"]})


async def case_07_search_ticket_simple_ack(client: httpx.AsyncClient) -> CaseResult:
    name = "07.search_ticket_simple_ack"
    user_id = "case07_search_ack"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="好的谢谢", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before ack should have one memory")
    assert_equal(len(after["recent"]), 1, "simple ack should not add memory after ticket")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"]})


async def case_08_search_to_qa_switch(client: httpx.AsyncClient) -> CaseResult:
    name = "08.search_to_qa_switch"
    user_id = "case08_ticket_switch"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="然后再帮我查一下我的积分", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 2, "switching ticket service should create new service memory")
    assert_equal(memory["last_service"].get("intent"), "qa", "积分查询应走信息查询路径")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_09_score_query(client: httpx.AsyncClient) -> CaseResult:
    name = "09.score_query"
    user_id = "case09_equity_score"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="帮我查一下我的积分", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(last.get("intent"), "qa", "积分查询应归类为qa")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_10_equity_upgrade_query(client: httpx.AsyncClient) -> CaseResult:
    name = "10.equity_upgrade_query"
    user_id = "case10_equity_upgrade"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="我想升级会员，帮我看看还差什么条件", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(last.get("intent"), "qa", "upgrade condition query should route to qa")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_11_refund_interrupt_starts_without_memory(client: httpx.AsyncClient) -> CaseResult:
    name = "11.refund_interrupt_starts_without_memory"
    user_id = "case11_refund_interrupt"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="我想退货", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 0, "interrupt before completion should not write service memory")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "interaction": resp.get("interaction")})


async def case_12_refund_resume_complete_with_trace(client: httpx.AsyncClient) -> CaseResult:
    name = "12.refund_resume_complete_with_trace"
    user_id = "case12_refund_resume"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="我想退货", thread_id=thread_id)
    resp = await post_chat(
        client,
        user_id=user_id,
        message="订单号 N20260306000034，退那双袜子，数量 1，原因是不想要了",
        thread_id=thread_id,
    )
    interaction = resp.get("interaction") or {}
    items = interaction.get("items") or []
    if items:
        confirm_key = str(items[0].get("key") or "").strip()
        assert_true(bool(confirm_key), "refund confirmation should provide selectable item key")
        await post_chat(client, user_id=user_id, message=confirm_key, thread_id=thread_id)
    else:
        await post_chat(client, user_id=user_id, message="确认", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(len(memory["recent"]), 1, "refund resume should end with one memory")
    assert_equal(last.get("intent"), "ticket", "refund should be ticket")
    assert_equal(service_key(last), "refund-ticket", "refund resume should route to refund-ticket")
    assert_true(trace_len(last) >= 1, "refund ticket should have trace")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_13_refund_simple_ack(client: httpx.AsyncClient) -> CaseResult:
    name = "13.refund_simple_ack"
    user_id = "case13_refund_ack"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="我想退货", thread_id=thread_id)
    await post_chat(
        client,
        user_id=user_id,
        message="订单号 N20260306000034，退那双袜子，数量 1，原因是不想要了",
        thread_id=thread_id,
    )
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="好的谢谢", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "refund should complete before ack")
    assert_equal(len(after["recent"]), 1, "refund simple ack should not add memory")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"]})


async def case_14_select_ticket_interaction_returned(client: httpx.AsyncClient) -> CaseResult:
    name = "14.select_ticket_interaction_returned"
    user_id = "case14_select_ticket"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="帮我查一下我之前那个工单的进度", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    interaction = resp.get("interaction") or {}
    reply = str(resp.get("reply") or "").strip()
    assert_true(bool(interaction) or bool(reply), "search without ticket id should at least return clarification or interaction")
    assert_true("暂时较忙" not in reply, "ambiguous search should not degrade into generic unavailable reply")
    if memory["last_service"]:
        assert_equal(service_key(memory["last_service"]), "search-ticket", "ambiguous search should stay on search-ticket if completed")
    return CaseResult(name=name, passed=True, details={"reply": reply, "interaction": interaction, "last_service": memory["last_service"]})


async def case_15_interaction_selection_archives_interrupt(client: httpx.AsyncClient) -> CaseResult:
    name = "15.interaction_selection_archives_interrupt"
    user_id = "case15_interaction_archive"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    first = await post_chat(client, user_id=user_id, message="帮我查一下我之前那个工单的进度", thread_id=thread_id)
    interaction = first.get("interaction") or {}
    items = interaction.get("items") or []
    if items:
        followup = str(items[0].get("key") or "").strip()
        assert_true(bool(followup), "interaction selection should contain key")
    else:
        followup = "是那个电饭煲内胆破损的工单"
    second = await post_chat(client, user_id=user_id, message=followup, thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    archived_messages = memory["archived_messages"]
    assert_equal(len(memory["recent"]), 1, "interaction selection should end in one service memory")
    assert_equal(service_key(last), "search-ticket", "selected interaction should still be search-ticket")
    if items:
        assert_true(any("[interaction]" in str(item.get("content") or "") for item in archived_messages), "archived messages should retain interrupt interaction payload when interaction is used")
    return CaseResult(name=name, passed=True, details={"reply": second["reply"], "archived_messages_count": len(archived_messages), "used_interaction": bool(items)})


async def case_16_interaction_followup_merge(client: httpx.AsyncClient) -> CaseResult:
    name = "16.interaction_followup_merge"
    user_id = "case16_interaction_merge"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    first = await post_chat(client, user_id=user_id, message="帮我查一下我之前那个工单的进度", thread_id=thread_id)
    interaction = first.get("interaction") or {}
    items = interaction.get("items") or []
    if items:
        followup = str(items[0].get("key") or "").strip()
        assert_true(bool(followup), "interaction selection should contain key")
    else:
        followup = "是那个电饭煲内胆破损的工单"
    await post_chat(client, user_id=user_id, message=followup, thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="那最新进展是什么？", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before same-service followup should have one memory")
    assert_equal(len(after["recent"]), 1, "interaction followup should merge same service")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"]})


async def case_17_interrupt_offtopic_cancels_current_service(client: httpx.AsyncClient) -> CaseResult:
    name = "17.interrupt_offtopic_cancels_current_service"
    user_id = "case17_interrupt_cancel"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="我想退货", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="顺便帮我查一下积分", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 0, "offtopic during pending service should not force-write service memory")
    reply = str(resp.get("reply") or "")
    assert_true("积分" in reply, "reply should explain积分需求需要后续单独处理")
    assert_true("退货" in reply, "reply should guide the user back to the current refund task")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_18_cancel_then_new_service(client: httpx.AsyncClient) -> CaseResult:
    name = "18.cancel_then_new_service"
    user_id = "case18_cancel_then_new"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="我想退货", thread_id=thread_id)
    await post_chat(client, user_id=user_id, message="顺便帮我查一下积分", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="那就帮我查积分吧", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 0, "switching away from a pending refund task should not silently complete the current service")
    reply = str(resp.get("reply") or "")
    assert_true("积分" in reply, "reply should guide user to re-initiate积分服务 in next turn")
    assert_true(
        ("退货" in reply) or ("当前" in reply) or ("服务范围" in reply),
        "reply should clearly explain the current task boundary before redirecting",
    )
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_19_qa_to_ticket_switch(client: httpx.AsyncClient) -> CaseResult:
    name = "19.qa_to_ticket_switch"
    user_id = "case19_qa_to_ticket"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="会员等级都有什么区别？", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="那帮我查一下工单 TK202604150001", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 2, "qa to ticket should create two service memories")
    assert_equal(memory["last_service"].get("intent"), "ticket", "second service should be ticket")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_20_sliding_window_keeps_last_10(client: httpx.AsyncClient) -> CaseResult:
    name = "20.sliding_window_keeps_last_10"
    user_id = "case20_window"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    messages = [
        "会员升级都有哪些条件？",
        "积分可以提现吗？",
        "如何申请开发票？",
        "售后电话是多少？",
        "如何修改收货地址？",
        "会员日有哪些权益？",
        "积分什么时候过期？",
        "换货时效是多久？",
        "线下门店怎么查？",
        "会员积分如何累计？",
        "订单发货后还能改地址吗？",
        "发票抬头可以修改吗？",
        "包裹签收后还能申请售后吗？",
    ]
    completed_service_ids: List[str] = []
    turn_results: List[Dict[str, Any]] = []

    previous_memory = await inspect_user_memory(user_id, thread_id)
    previous_ids = recent_service_ids(previous_memory)
    for message in messages:
        _, current_memory = await post_and_inspect(
            client,
            user_id=user_id,
            thread_id=thread_id,
            message=message,
        )
        current_ids = recent_service_ids(current_memory)
        current_last_id = service_id(current_memory.get("last_service"))
        assert_true(len(current_ids) <= 10, "recent should never exceed the window size")

        completed = bool(current_ids) and (not previous_ids or current_ids[-1] != previous_ids[-1])
        if completed:
            completed_service_ids.append(current_ids[-1])

        turn_results.append(
            {
                "message": message,
                "completed": completed,
                "recent_count": len(current_ids),
                "last_service_id": current_last_id,
            }
        )
        previous_ids = current_ids

    memory = await inspect_user_memory(user_id, thread_id)
    current_ids = recent_service_ids(memory)
    assert_true(
        len(completed_service_ids) >= 12,
        "window case should accumulate at least 12 completed services before asserting eviction",
    )
    assert_equal(len(current_ids), 10, "sliding window should keep latest 10 completed service memories")
    assert_equal(current_ids, completed_service_ids[-10:], "recent should match the latest 10 completed services")
    return CaseResult(
        name=name,
        passed=True,
        details={
            "recent_count": len(current_ids),
            "completed_services": len(completed_service_ids),
            "turn_results": turn_results,
        },
    )


async def case_21_qa_followup_merge(client: httpx.AsyncClient) -> CaseResult:
    name = "21.qa_followup_merge"
    user_id = "case21_qa_followup"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="会员升级都有哪些条件？", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="那 gold 会员和 silver 会员有什么差别？", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before qa followup should have one memory")
    assert_equal(len(after["recent"]), 1, "qa followup should merge into same memory")
    assert_equal(after["last_service"].get("intent"), "qa", "qa followup should stay in qa")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": after["last_service"]})


async def case_22_qa_to_recommend_switch(client: httpx.AsyncClient) -> CaseResult:
    name = "22.qa_to_recommend_switch"
    user_id = "case22_qa_to_recommend"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="会员升级都有哪些条件？", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="那给我推荐一款适合送人的小家电", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 2, "qa to recommend should create two service memories")
    assert_equal(memory["last_service"].get("intent"), "recommend", "second service should be recommend")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_23_recommend_followup_merge(client: httpx.AsyncClient) -> CaseResult:
    name = "23.recommend_followup_merge"
    user_id = "case23_recommend_followup"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="给我推荐一款适合送人的小家电", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="预算控制在 300 以内呢？", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before recommend followup should have one memory")
    assert_equal(len(after["recent"]), 1, "recommend followup should merge into same memory")
    assert_equal(after["last_service"].get("intent"), "recommend", "recommend followup should stay in recommend")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": after["last_service"]})


async def case_24_recommend_to_qa_switch(client: httpx.AsyncClient) -> CaseResult:
    name = "24.recommend_to_qa_switch"
    user_id = "case24_recommend_to_qa"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="给我推荐一款适合送人的小家电", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="那会员升级都有哪些条件？", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 2, "recommend to qa should create two service memories")
    assert_equal(memory["last_service"].get("intent"), "qa", "second service should be qa")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_25_equity_upgrade_apply(client: httpx.AsyncClient) -> CaseResult:
    name = "25.equity_upgrade_apply"
    user_id = "case25_equity_apply"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    first = await post_chat(client, user_id=user_id, message="我想申请升级会员", thread_id=thread_id)
    first_memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(first_memory["recent"]), 0, "upgrade apply should clarify before completion")
    assert_true("升级" in str(first.get("reply") or ""), "upgrade apply should stay in upgrade context while clarifying")

    second = await post_chat(
        client,
        user_id=user_id,
        message="我想升级到铂金会员，原因是近期消费较高，希望发起人工审核申请",
        thread_id=thread_id,
    )
    memory = await inspect_user_memory(user_id, thread_id)
    if not memory["last_service"]:
        followup = (
            first_interaction_key(second)
            if second.get("interaction")
            else "确认申请升级到铂金会员，原因是近期消费较高，希望人工审核"
        )
        await post_chat(client, user_id=user_id, message=followup, thread_id=thread_id)
        memory = await inspect_user_memory(user_id, thread_id)

    last = memory["last_service"]
    assert_true(isinstance(last, dict) and bool(last), "upgrade apply should complete after clarifying target level and reason")
    assert_equal(last.get("intent"), "ticket", "upgrade apply should be ticket")
    assert_equal(service_key(last), "equity-ticket", "upgrade apply should route to equity-ticket")
    assert_true(trace_len(last) >= 1, "equity ticket should have trace")
    return CaseResult(
        name=name,
        passed=True,
        details={
            "first_reply": first["reply"],
            "second_reply": second["reply"],
            "last_service": last,
        },
    )


async def case_26_equity_upgrade_appeal(client: httpx.AsyncClient) -> CaseResult:
    name = "26.equity_upgrade_appeal"
    user_id = "case26_equity_appeal"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    resp = await post_chat(client, user_id=user_id, message="我的会员升级失败了，帮我申诉一下", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_equal(last.get("intent"), "ticket", "upgrade appeal should be ticket")
    assert_equal(service_key(last), "equity-ticket", "upgrade appeal should route to equity-ticket")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": last})


async def case_27_ticket_polite_ack_variant(client: httpx.AsyncClient) -> CaseResult:
    name = "27.ticket_polite_ack_variant"
    user_id = "case27_ticket_ack_variant"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    before = await inspect_user_memory(user_id, thread_id)
    resp = await post_chat(client, user_id=user_id, message="嗯嗯，知道啦，谢谢你", thread_id=thread_id)
    after = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(before["recent"]), 1, "before ticket ack variant should have one memory")
    assert_equal(len(after["recent"]), 1, "polite ack variant should not add memory")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"]})


async def case_28_refund_interrupt_return_to_task(client: httpx.AsyncClient) -> CaseResult:
    name = "28.refund_interrupt_return_to_task"
    user_id = "case28_refund_return"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    first = await post_chat(client, user_id=user_id, message="我想退货", thread_id=thread_id)
    interaction = first.get("interaction") or {}
    items = interaction.get("items") or []
    if items:
        chosen = str(items[0].get("key") or "").strip()
        assert_true(bool(chosen), "refund order selection should provide key")
    else:
        chosen = "订单号 N20260306000034"
    second = await post_chat(client, user_id=user_id, message=chosen, thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 0, "returning to pending refund task should not finish service yet")
    assert_true(bool(second.get("interaction") or {}) or "退货" in str(second.get("reply") or ""), "refund flow should continue after returning to original task")
    return CaseResult(name=name, passed=True, details={"reply": second["reply"], "interaction": second.get("interaction")})


async def case_29_ticket_to_qa_switch(client: httpx.AsyncClient) -> CaseResult:
    name = "29.ticket_to_qa_switch"
    user_id = "case29_ticket_to_qa"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    resp = await post_chat(client, user_id=user_id, message="会员升级都有哪些条件？", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    assert_equal(len(memory["recent"]), 2, "ticket to qa should create two service memories")
    assert_equal(memory["last_service"].get("intent"), "qa", "second service should be qa")
    return CaseResult(name=name, passed=True, details={"reply": resp["reply"], "last_service": memory["last_service"]})


async def case_30_last_service_points_to_latest(client: httpx.AsyncClient) -> CaseResult:
    name = "30.last_service_points_to_latest"
    user_id = "case30_last_service"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)
    messages = [
        "会员升级都有哪些条件？",
        "积分可以提现吗？",
        "如何申请开发票？",
    ]
    completed_service_ids: List[str] = []
    previous_memory = await inspect_user_memory(user_id, thread_id)
    previous_ids = recent_service_ids(previous_memory)

    for message in messages:
        _, current_memory = await post_and_inspect(
            client,
            user_id=user_id,
            thread_id=thread_id,
            message=message,
        )
        current_ids = recent_service_ids(current_memory)
        if current_ids and (not previous_ids or current_ids[-1] != previous_ids[-1]):
            completed_service_ids.append(current_ids[-1])
        previous_ids = current_ids

    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    latest_recent = latest_recent_service(memory)
    assert_true(
        len(completed_service_ids) >= 2,
        "last_service case should observe multiple completed services before comparing the latest pointer",
    )
    assert_true(bool(latest_recent), "recent should contain at least one completed service")
    assert_equal(service_id(last), service_id(latest_recent), "last_service should point to the latest completed memory")
    return CaseResult(
        name=name,
        passed=True,
        details={
            "completed_services": len(completed_service_ids),
            "last_service": last,
        },
    )


async def case_31_ticket_service_key_contract(client: httpx.AsyncClient) -> CaseResult:
    name = "31.ticket_service_key_contract"
    user_id = "case31_ticket_contract"
    thread_id = f"{name}_{uuid.uuid4().hex[:8]}"
    await clear_user_memory(user_id)

    state_fields = set(getattr(AgentState, "__annotations__", {}).keys())
    assert_true("service_key" in state_fields, "ticket state should keep service_key")
    assert_true("skill_location" not in state_fields, "ticket state should not require skill_location")
    assert_true("available_tools" not in state_fields, "ticket state should not require available_tools")

    resp = await post_chat(client, user_id=user_id, message="帮我查一下工单 TK202604150001 的处理进度", thread_id=thread_id)
    memory = await inspect_user_memory(user_id, thread_id)
    last = memory["last_service"]
    assert_true(isinstance(last, dict) and bool(last), "ticket contract case should complete with service memory")
    assert_equal(last.get("intent"), "ticket", "ticket contract case should stay in ticket")
    assert_equal(service_key(last), "search-ticket", "ticket contract case should archive service_key in service memory")
    return CaseResult(
        name=name,
        passed=True,
        details={
            "reply": resp["reply"],
            "state_fields": sorted(state_fields),
            "last_service": last,
        },
    )


CASES: List[CaseFunc] = [
    case_01_qa_basic,
    case_02_qa_simple_ack,
    case_03_recommend_basic,
    case_04_recommend_simple_ack,
    case_05_search_ticket_basic,
    case_06_search_ticket_followup_merge,
    case_07_search_ticket_simple_ack,
    case_08_search_to_qa_switch,
    case_09_score_query,
    case_10_equity_upgrade_query,
    case_11_refund_interrupt_starts_without_memory,
    case_12_refund_resume_complete_with_trace,
    case_13_refund_simple_ack,
    case_14_select_ticket_interaction_returned,
    case_15_interaction_selection_archives_interrupt,
    case_16_interaction_followup_merge,
    case_17_interrupt_offtopic_cancels_current_service,
    case_18_cancel_then_new_service,
    case_19_qa_to_ticket_switch,
    case_20_sliding_window_keeps_last_10,
    case_21_qa_followup_merge,
    case_22_qa_to_recommend_switch,
    case_23_recommend_followup_merge,
    case_24_recommend_to_qa_switch,
    case_25_equity_upgrade_apply,
    case_26_equity_upgrade_appeal,
    case_27_ticket_polite_ack_variant,
    case_28_refund_interrupt_return_to_task,
    case_29_ticket_to_qa_switch,
    case_30_last_service_points_to_latest,
    case_31_ticket_service_key_contract,
]


async def run_case(client: httpx.AsyncClient, case_func: CaseFunc) -> CaseResult:
    try:
        return await case_func(client)
    except Exception as exc:
        return CaseResult(name=case_func.__name__, passed=False, error=str(exc))


async def main() -> None:
    async with httpx.AsyncClient(base_url=API_BASE_URL) as client:
        results: List[CaseResult] = []
        for case_func in CASES:
            result = await run_case(client, case_func)
            results.append(result)
            status = "PASS" if result.passed else "FAIL"
            print(f"[{status}] {result.name}")
            if result.error:
                print(f"  error: {result.error}")
            if result.details:
                print(f"  details: {json.dumps(result.details, ensure_ascii=False)}")

    passed = sum(1 for item in results if item.passed)
    failed = len(results) - passed
    print("\n=== SUMMARY ===")
    print(json.dumps(
        {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "failed_cases": [item.name for item in results if not item.passed],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
