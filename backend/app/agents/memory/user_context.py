from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from app.agents.memory.user_facts import load_user_facts
from app.agents.memory.user_profile import load_user_profile
from app.config.logging import get_logger


logger = get_logger("user_context")


async def _load_profile_summary(user_id: str) -> str:
    profile = await load_user_profile(user_id)
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("summary") or "").strip()


async def _load_user_facts(user_id: str) -> list[str]:
    try:
        raw_facts = await load_user_facts(user_id)
    except Exception as e:
        logger.warning("[user_context] user facts load failed for %s: %s", user_id, e)
        return []
    return [str(item).strip() for item in raw_facts or [] if str(item).strip()]


async def load_user_context(user_id: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
    profile_summary, user_facts = await asyncio.gather(
        _load_profile_summary(user_id),
        _load_user_facts(user_id),
    )
    context = {
        "profile_summary": profile_summary,
        "user_facts": user_facts or [],
    }
    logger.info("[user_context] loaded context for %s", user_id)
    return context
