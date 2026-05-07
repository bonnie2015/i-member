from __future__ import annotations

import json
from typing import Any, Dict, Optional

from app.agents.summary_agent import summary_agent
from app.config.logging import get_logger
from app.config.redis import get_optional_redis_client
from app.config.redis_keys import USER_PROFILE_CACHE_KEY

logger = get_logger("user_profile")

_PROFILE_CACHE_TTL_SECONDS = 2 * 60 * 60


def _profile_cache_key(user_id: str, fields: Optional[str] = None) -> str:
    normalized_fields = str(fields or "").strip()
    fields_key = normalized_fields or "full"
    return USER_PROFILE_CACHE_KEY.format(user_id=user_id, fields_key=fields_key)


async def _build_profile_summary(profile: Dict[str, Any]) -> str:
    return await summary_agent.summarize_profile(profile)


async def load_user_profile(user_id: str, fields: Optional[str] = None) -> Dict[str, Any]:
    from app.tools.business.scrm_tools import call_scrm_api

    cache_key = _profile_cache_key(user_id, fields)
    redis = await get_optional_redis_client()
    try:
        if redis:
            cached = await redis.get(cache_key)
            if cached:
                try:
                    parsed = json.loads(cached)
                    if isinstance(parsed, dict):
                        parsed.pop("_raw", None)
                        return parsed
                except Exception:
                    logger.warning("[user_profile] invalid cache for %s", cache_key)

        profile = await call_scrm_api(
            "get_user_profile",
            {"user_id": user_id, **({"fields": fields} if str(fields or "").strip() else {})},
        )
        if not isinstance(profile, dict) or "error" in profile or "error_code" in profile:
            return profile if isinstance(profile, dict) else {}

        profile.pop("_raw", None)
        summary = await _build_profile_summary(profile)
        payload = {**profile, "summary": summary}
        if redis:
            await redis.setex(cache_key, _PROFILE_CACHE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
        return payload
    except Exception as e:
        logger.warning("[user_profile] load failed for user_id=%s: %s", user_id, e)
        return {}
