"""
user_context.py — 用户上下文预加载服务

服务前通过 SCRM 接口加载用户画像、会员等级、标签，
并从长期记忆中读取历史记忆片段。

缓存策略（Redis）：
  - 用户画像 / 会员等级 / 标签：5 分钟
  - 行为记录：1 分钟
"""

import json
import asyncio
import re
from typing import Any, Dict, List, Optional

from app.config.config import settings
from app.config.logging import get_logger
from app.agents.memory.redis_keys import user_behavior_key, user_profile_key
from app.agents.tools.scrm_tools import call_scrm_api

logger = get_logger("user_context")
_USER_PROFILE_TTL = 300
_USER_BEHAVIOR_TTL = 60

_ORDER_ID_RE = re.compile(r"\b(?:ORD[-_]?)?\d{5,}\b", re.IGNORECASE)
_TICKET_ID_RE = re.compile(r"\bTK[-_][A-Z0-9-]+\b", re.IGNORECASE)


async def _get_redis():
    """获取 Redis 异步客户端，失败时返回 None。"""
    try:
        from redis.asyncio import Redis as AsyncRedis
        client = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception:
        return None


def _extract_first_match(pattern, text: str) -> str:
    if not text:
        return ""
    match = pattern.search(text)
    return match.group(0) if match else ""


def _enrich_service_record(service: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(service)
    msg_text = " ".join(str(msg.get("content", "")) for msg in service.get("messages", []))
    reply_text = str(service.get("final_reply", ""))
    merged_text = f"{msg_text}\n{reply_text}"

    if "order_id" not in enriched:
        enriched["order_id"] = _extract_first_match(_ORDER_ID_RE, merged_text)
    if "ticket_id" not in enriched:
        enriched["ticket_id"] = _extract_first_match(_TICKET_ID_RE, merged_text)
    structured_summary = enriched.get("service_summary_structured") or {}
    if structured_summary:
        point_info = structured_summary.get("point_info") or {}
        enriched.setdefault("goal", structured_summary.get("goal", ""))
        enriched.setdefault("result", structured_summary.get("result", ""))
        if point_info.get("order_id") and not enriched.get("order_id"):
            enriched["order_id"] = point_info.get("order_id")
        if point_info.get("ticket_id") and not enriched.get("ticket_id"):
            enriched["ticket_id"] = point_info.get("ticket_id")
    if not enriched.get("service_summary"):
        enriched["service_summary"] = reply_text[:120] if reply_text else ""

    return enriched


def _format_service_messages(messages: List[Dict[str, Any]], limit: int = 6) -> List[str]:
    formatted = []
    for msg in messages[-limit:]:
        role = "用户" if msg.get("role") == "user" else "助手"
        content = str(msg.get("content", "")).strip()
        if content:
            formatted.append(f"  - {role}：{content}")
    return formatted


def _trim_text(text: Any, limit: int = 120) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _normalize_memory_item(memory: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "content": _trim_text(memory.get("content", ""), 120),
        "confidence": float(memory.get("confidence", 0.0) or 0.0),
        "created_at": memory.get("created_at", ""),
    }


def build_ticket_profile_minimal(context: Dict[str, Any]) -> Dict[str, Any]:
    profile = context.get("profile") or {}
    level_detail = profile.get("level_detail") or {}
    tags = profile.get("tags") or profile.get("tags_detail") or []
    if isinstance(tags, dict):
        tags = list(tags.values())
    if not isinstance(tags, list):
        tags = [str(tags)]

    return {
        "name": profile.get("name", ""),
        "member_level": profile.get("member_level", ""),
        "tags": [str(tag) for tag in tags[:5] if tag],
        "score": level_detail.get("score", level_detail.get("points")),
        "score_to_next": level_detail.get("score_to_next", level_detail.get("points_to_next")),
    }


def build_ticket_continuity_context(
    context: Dict[str, Any],
    is_continuous: bool = False,
) -> Dict[str, Any]:
    if not is_continuous:
        return {}

    last_service = context.get("last_service") or {}
    if not last_service:
        return {}

    return {
        "intent": last_service.get("intent", "unknown"),
        "order_id": last_service.get("order_id", ""),
        "ticket_id": last_service.get("ticket_id", ""),
        "goal": (last_service.get("service_summary_structured") or {}).get("goal", ""),
        "result": (last_service.get("service_summary_structured") or {}).get("result", ""),
        "point_info": (last_service.get("service_summary_structured") or {}).get("point_info", {}),
        "service_summary": _trim_text(
            (
                (last_service.get("service_summary_structured") or {}).get("message_summary")
                or last_service.get("service_summary")
                or last_service.get("final_reply")
                or ""
            ),
            160,
        ),
        "key_messages": _format_service_messages(last_service.get("messages", []), limit=4),
    }


def select_relevant_memories(
    context: Dict[str, Any],
    scene: Optional[str] = None,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    memories = context.get("memories") or []
    normalized = [_normalize_memory_item(memory) for memory in memories if memory.get("content")]
    if not scene:
        return normalized[:limit]

    scene_keywords = {
        "refund": ["退货", "换货", "售后", "退款", "订单"],
        "change": ["换货", "尺码", "颜色", "订单"],
        "quality": ["质量", "破损", "瑕疵", "凭证", "图片"],
        "complain": ["投诉", "服务", "物流", "态度", "体验"],
        "equity": ["权益", "升级", "积分", "会员", "等级"],
    }
    keywords = scene_keywords.get(scene, [])
    if not keywords:
        return normalized[:limit]

    matched = []
    fallback = []
    for memory in normalized:
        content = memory.get("content", "")
        if any(keyword in content for keyword in keywords):
            matched.append(memory)
        else:
            fallback.append(memory)
    return (matched + fallback)[:limit]


def build_ticket_context_layers(
    context: Dict[str, Any],
    is_continuous: bool = False,
    scene: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "profile_minimal": build_ticket_profile_minimal(context),
        "continuity_context": build_ticket_continuity_context(context, is_continuous=is_continuous),
        "relevant_memories": select_relevant_memories(context, scene=scene, limit=3),
    }


async def load_user_context(user_id: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
    """
    加载用户上下文，优先从 Redis 缓存读取。

    Returns:
        包含 profile、level、tags、behavior、memories 的字典。
    """
    redis = await _get_redis()
    context: Dict[str, Any] = {"user_id": user_id}

    # --- profile / level / tags（5 分钟缓存）---
    profile_key = user_profile_key(user_id)
    cached_profile: Optional[str] = None
    if redis:
        cached_profile = await redis.get(profile_key)

    if cached_profile:
        try:
            context["profile"] = json.loads(cached_profile)
            logger.info(f"[user_context] profile cache hit for {user_id}")
        except Exception:
            cached_profile = None

    if not cached_profile:
        try:
            profile, level, tags = await asyncio.gather(
                call_scrm_api("get_user_detail", {}),
                call_scrm_api("get_user_level", {}),
                call_scrm_api("get_user_tag", {}),
            )
            merged = {**profile, "level_detail": level, "tags_detail": tags}
            context["profile"] = merged
            if redis:
                await redis.setex(
                    profile_key,
                    _USER_PROFILE_TTL,
                    json.dumps(merged, ensure_ascii=False),
                )
        except Exception as e:
            logger.warning(f"[user_context] failed to load profile for {user_id}: {e}")
            context["profile"] = {}

    # --- behavior（1 分钟缓存）---
    behavior_key = user_behavior_key(user_id)
    cached_behavior: Optional[str] = None
    if redis:
        cached_behavior = await redis.get(behavior_key)

    if cached_behavior:
        try:
            context["behavior"] = json.loads(cached_behavior)
            logger.info(f"[user_context] behavior cache hit for {user_id}")
        except Exception:
            cached_behavior = None

    if not cached_behavior:
        # 行为记录不是首轮路由和 ticket 规划的关键依赖，避免阻塞首段响应
        context["behavior"] = {}

    # --- 长期记忆 + 最近服务历史（并发，本地依赖）---
    try:
        from app.agents.memory.long_term_memory import load_memories
        from app.agents.memory.service_history import get_recent_services

        memories, services = await asyncio.gather(
            load_memories(user_id),
            get_recent_services(user_id),
            return_exceptions=True,
        )

        if isinstance(memories, Exception):
            logger.warning(f"[user_context] failed to load memories for {user_id}: {memories}")
            context["memories"] = []
        else:
            context["memories"] = memories

        if isinstance(services, Exception):
            logger.warning(f"[user_context] failed to load service history for {user_id}: {services}")
            context["recent_services"] = []
            context["last_service"] = None
            context["service_summary"] = ""
        else:
            recent_services = [_enrich_service_record(svc) for svc in services if svc.get("type") != "summary"]
            summary_items = [svc for svc in services if svc.get("type") == "summary"]
            context["recent_services"] = recent_services[-2:]
            context["last_service"] = recent_services[-1] if recent_services else None
            context["service_summary"] = summary_items[0].get("content", "") if summary_items else ""
    except Exception as e:
        logger.warning(f"[user_context] failed to load local context for {user_id}: {e}")
        context["memories"] = []
        context["recent_services"] = []
        context["last_service"] = None
        context["service_summary"] = ""

    if redis:
        await redis.aclose()

    context["ticket_context_layers"] = build_ticket_context_layers(context, is_continuous=False, scene=None)
    logger.info(f"[user_context] loaded context for {user_id}: level={context.get('profile', {}).get('member_level')}")
    return context


def format_context_for_prompt(context: Dict[str, Any]) -> str:
    """将用户上下文格式化为 system prompt 可注入的文本。"""
    lines = []
    profile = context.get("profile", {})
    if profile:
        lines.append(f"会员姓名：{profile.get('name', '未知')}")
        lines.append(f"会员等级：{profile.get('member_level', '未知')}")
        level_detail = profile.get("level_detail", {})
        if level_detail:
            current_score = level_detail.get("score", level_detail.get("points", 0))
            score_to_next = level_detail.get("score_to_next", level_detail.get("points_to_next", "?"))
            lines.append(f"积分：{current_score}，距下一级还需 {score_to_next} 分")
        tags = profile.get("tags", [])
        if tags:
            lines.append(f"用户标签：{', '.join(tags)}")

    memories = context.get("memories", [])
    if memories:
        lines.append("\n用户历史记忆：")
        for mem in memories[:5]:  # 最多注入 5 条
            lines.append(f"  - {mem.get('content', '')}")

    recent_services = context.get("recent_services", [])
    if recent_services:
        lines.append("\n最近服务记录：")
        for svc in recent_services[-2:]:
            summary = svc.get("service_summary") or svc.get("final_reply") or ""
            if summary:
                lines.append(f"  - [{svc.get('intent', 'unknown')}] {summary}")

    service_summary = context.get("service_summary", "")
    if service_summary:
        lines.append("\n历史服务摘要：")
        lines.append(service_summary)

    return "\n".join(lines) if lines else "暂无用户信息"


def format_ticket_context_for_prompt(
    context: Dict[str, Any],
    is_continuous: bool = False,
    scene: Optional[str] = None,
) -> str:
    """ticket 子图专用上下文：按优先级格式化分层上下文。"""
    layers = build_ticket_context_layers(context, is_continuous=is_continuous, scene=scene)
    lines = []
    profile = layers.get("profile_minimal") or {}
    continuity = layers.get("continuity_context") or {}
    memories = layers.get("relevant_memories") or []

    if profile:
        lines.append("P5 静态画像：")
        lines.append(f"  - 会员姓名：{profile.get('name') or '未知'}")
        lines.append(f"  - 会员等级：{profile.get('member_level') or '未知'}")
        if profile.get("tags"):
            lines.append(f"  - 用户标签：{', '.join(profile['tags'])}")
        if profile.get("score") is not None:
            lines.append(f"  - 当前积分：{profile['score']}")
        if profile.get("score_to_next") is not None:
            lines.append(f"  - 距离下一级：{profile['score_to_next']}")

    if continuity:
        lines.append("\nP3 最近 1 轮连续服务：")
        lines.append(f"  - 意图：{continuity.get('intent') or 'unknown'}")
        if continuity.get("order_id"):
            lines.append(f"  - 关联订单：{continuity['order_id']}")
        if continuity.get("ticket_id"):
            lines.append(f"  - 关联工单：{continuity['ticket_id']}")
        if continuity.get("service_summary"):
            lines.append(f"  - 处理结果：{continuity['service_summary']}")
        if continuity.get("key_messages"):
            lines.append("  - 关键对话：")
            lines.extend(continuity["key_messages"])

    if memories:
        lines.append("\nP4 相关长期记忆：")
        for mem in memories:
            lines.append(f"  - {mem.get('content', '')}")

    return "\n".join(lines) if lines else "暂无用户信息"
