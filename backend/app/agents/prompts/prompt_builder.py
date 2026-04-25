from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Mapping

from pydantic import BaseModel, ConfigDict, Field
from app.agents.tools.scrm_client import REQUEST_USER_ID_CTX

_PROMPTS_DIR = Path(__file__).parent
_PREFIX_FILE = _PROMPTS_DIR / "prompt_prefix.txt"


class PromptUserContext(BaseModel):
    user_id: str = ""
    service_memory_summary: str = ""
    user_facts: list[str] = Field(default_factory=list)
    profile_summary: str = ""


class PromptCapabilityContext(BaseModel):
    ticket_skills_snapshot: str = ""
    selected_skill_content: str = ""
    interaction_template: str = ""


class TicketRuntimePayload(BaseModel):
    current_time: str = ""
    current_round: str = ""
    max_rounds: str = ""
    execution_trace: str = ""


class PostProcessRuntimePayload(BaseModel):
    intent: str = ""
    reason: str = ""
    final_status: str = ""
    final_reply: str = ""
    trace: Any = ""
    facts: Any = ""


class UserFactsRuntimePayload(BaseModel):
    existing_core_facts: list[str] = Field(default_factory=list)
    current_service_memory_summary: str = ""


_PROMPT_CAPABILITY_FIELD_LABELS: dict[str, str] = {
    "ticket_skills_snapshot": "工单技能快照",
    "selected_skill_content": "当前选中技能",
    "interaction_template": "交互模板",
}


@lru_cache(maxsize=128)
def load_prompt(relative_path: str) -> str:

    prompt_path = Path(__file__).parent.parent / "prompts" / relative_path
    return prompt_path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def load_prompt_prefix() -> str:
    if not _PREFIX_FILE.exists():
        return ""
    return _PREFIX_FILE.read_text(encoding="utf-8").strip()


def format_context_sections(sections: Dict[str, Any]) -> str:
    blocks: list[str] = []
    for title, raw_value in sections.items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        blocks.append(f"{title}：\n\n{value}")
    return "\n\n".join(blocks)


def _normalize_text_list(items: list[str]) -> str:
    normalized = [str(item).strip() for item in items if str(item).strip()]
    if not normalized:
        return ""
    return "\n".join(f"- {item}" for item in normalized)


def _build_prompt_user_context(
    *,
    user_context: Mapping[str, Any] | None = None,
) -> PromptUserContext:
    loaded = user_context or {}
    user_facts = loaded.get("user_facts") or []
    return PromptUserContext(
        user_id=str(REQUEST_USER_ID_CTX.get() or "").strip(),
        service_memory_summary=str(loaded.get("service_memory_summary") or "").strip(),
        user_facts=[str(item).strip() for item in user_facts if str(item).strip()],
        profile_summary=str(loaded.get("profile_summary") or "").strip(),
    )


def _compact_sections(sections: Dict[str, Any]) -> Dict[str, Any]:
    compacted: Dict[str, Any] = {}
    for title, raw_value in sections.items():
        value = str(raw_value or "").strip()
        if value:
            compacted[title] = value
    return compacted


def format_user_context(user_context: PromptUserContext) -> str:
    sections = _compact_sections(
        {
            "当前服务用户": f"user_id = {user_context.user_id}" if str(user_context.user_id or "").strip() else "",
            "上一轮服务摘要": str(user_context.service_memory_summary or "").strip(),
            "用户长期事实": _normalize_text_list(list(user_context.user_facts or [])),
            "用户画像摘要": str(user_context.profile_summary or "").strip(),
        }
    )
    rendered = format_context_sections(sections)
    return rendered or "[None]"


def _load_prompt_capability_context(
    capability_context: PromptCapabilityContext | None,
) -> str:
    if capability_context is None:
        return "[None]"
    sections: Dict[str, Any] = {}
    for field_name in PromptCapabilityContext.model_fields:
        label = _PROMPT_CAPABILITY_FIELD_LABELS.get(field_name)
        if not label:
            continue
        raw_value = getattr(capability_context, field_name, None)
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if not value:
            continue
        sections[label] = value
    rendered = format_context_sections(sections)
    return rendered or "[None]"


def with_prompt_prefix(system_prompt: str) -> str:
    body = str(system_prompt or "").strip()
    prefix = load_prompt_prefix()
    if not prefix:
        return body
    if not body:
        return prefix
    return f"{prefix}\n\n{body}"


def build_base_system_prompt(
    *,
    prompt_file: str,
    **kwargs: Any,
) -> str:
    template = load_prompt(prompt_file)
    rendered = template.format(**dict(kwargs))
    return with_prompt_prefix(rendered)


def build_ticket_runtime_context(
    payload: TicketRuntimePayload | None = None,
) -> str:
    runtime_payload = payload or TicketRuntimePayload()
    rendered = format_context_sections(
        {
            "当前时间": str(runtime_payload.current_time or "").strip(),
            "当前轮次": str(runtime_payload.current_round or "").strip(),
            "最大轮次": str(runtime_payload.max_rounds or "").strip(),
            "已执行步骤": str(runtime_payload.execution_trace or "").strip(),
        }
    )
    return rendered or "[None]"


def build_post_process_runtime_context(
    payload: PostProcessRuntimePayload | None = None,
) -> str:
    runtime_payload = payload or PostProcessRuntimePayload()
    rendered = format_context_sections(
        {
            "当前意图": str(runtime_payload.intent or "").strip(),
            "当前判断原因": str(runtime_payload.reason or "").strip(),
            "最终状态": str(runtime_payload.final_status or "").strip(),
            "最终答复": str(runtime_payload.final_reply or "").strip(),
            "执行轨迹": str(runtime_payload.trace or "").strip(),
            "关键事实": str(runtime_payload.facts or "").strip(),
        }
    )
    return rendered or "[None]"


def build_user_facts_runtime_context(
    payload: UserFactsRuntimePayload | None = None,
) -> str:
    runtime_payload = payload or UserFactsRuntimePayload()
    rendered = format_context_sections(
        {
            "已有重要长期事实": _normalize_text_list(list(runtime_payload.existing_core_facts or [])),
            "本轮服务记忆摘要": str(runtime_payload.current_service_memory_summary or "").strip(),
        }
    )
    return rendered or "[None]"


async def build_router_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    return build_base_system_prompt(
        prompt_file="router/router.txt",
        user_context=rendered_user_context,
        capability_context=rendered_capability_context,
    )


async def build_ticket_guard_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    return build_base_system_prompt(
        prompt_file="ticket/guard.txt",
        user_context=rendered_user_context,
        capability_context=rendered_capability_context,
    )


async def build_ticket_thinker_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    runtime_payload: TicketRuntimePayload | None = None,
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    rendered_runtime_context = build_ticket_runtime_context(runtime_payload)
    return build_base_system_prompt(
        prompt_file="ticket/thinker.txt",
        user_context=rendered_user_context,
        capability_context=rendered_capability_context,
        runtime_context=rendered_runtime_context,
    )


async def build_post_process_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    runtime_payload: PostProcessRuntimePayload | None = None,
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    rendered_runtime_context = build_post_process_runtime_context(runtime_payload)
    return build_base_system_prompt(
        prompt_file="post_process/service_memory.txt",
        user_context=rendered_user_context,
        capability_context=rendered_capability_context,
        runtime_context=rendered_runtime_context,
    )


async def build_user_profile_summary_system_prompt() -> str:
    return build_base_system_prompt(
        prompt_file="profile/summary.txt",
    )


async def build_user_facts_extraction_system_prompt(
    *,
    runtime_payload: UserFactsRuntimePayload | None = None,
) -> str:
    rendered_runtime_context = build_user_facts_runtime_context(runtime_payload)
    return build_base_system_prompt(
        prompt_file="post_process/user_facts.txt",
        runtime_context=rendered_runtime_context,
    )
