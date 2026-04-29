from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from app.agents.tools.business.execution_context import REQUEST_USER_ID_CTX

_PROMPTS_DIR = Path(__file__).parent
_PREFIX_FILE = _PROMPTS_DIR / "prompt_prefix.txt"
_APP_TIMEZONE = "Asia/Shanghai"


class PromptUserContext(BaseModel):
    user_id: str = ""
    user_facts: list[str] = Field(default_factory=list)
    profile_summary: str = ""


class PromptCapabilityContext(BaseModel):
    ticket_skills_snapshot: str = ""
    selected_skill_content: str = ""
    interaction_template: str = ""
    tool_summary: str = ""


class TicketPlanRuntimePayload(BaseModel):
    planning_mode: str = ""
    current_goal: str = ""
    slots: Dict[str, Any] = Field(default_factory=dict)
    failed_step: Dict[str, Any] = Field(default_factory=dict)


class TicketExecuteRuntimePayload(BaseModel):
    goal: str = ""
    step: Dict[str, Any] = Field(default_factory=dict)
    slots: Dict[str, Any] = Field(default_factory=dict)
    expected_slots: list[str] = Field(default_factory=list)


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


class RecommendGuardRuntimePayload(BaseModel):
    recommend_context: Dict[str, Any] = Field(default_factory=dict)
    last_trace: Dict[str, Any] = Field(default_factory=dict)


_PROMPT_CAPABILITY_FIELD_LABELS: dict[str, str] = {
    "ticket_skills_snapshot": "工单技能快照",
    "selected_skill_content": "当前选中技能",
    "interaction_template": "交互模板",
    "tool_summary": "可用工具摘要",
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
            "用户长期事实": _normalize_text_list(list(user_context.user_facts or [])),
            "用户画像摘要": str(user_context.profile_summary or "").strip(),
        }
    )
    rendered = format_context_sections(sections)
    return rendered or "[None]"


def render_capability_context(
    capability_context: PromptCapabilityContext | None,
) -> str:
    return _load_prompt_capability_context(capability_context)


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
    now = datetime.now(ZoneInfo(_APP_TIMEZONE))
    time_context = f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}（{_APP_TIMEZONE}）"
    prefix = load_prompt_prefix()
    prefix_parts = []
    if prefix:
        prefix_parts.append(prefix)
    prefix_parts.append(time_context)
    rendered_prefix = "\n\n".join(prefix_parts)
    if not body:
        return rendered_prefix
    return f"{rendered_prefix}\n\n{body}"


def build_base_system_prompt(
    *,
    prompt_file: str,
    **kwargs: Any,
) -> str:
    template = load_prompt(prompt_file)
    rendered = template.format(**dict(kwargs))
    return with_prompt_prefix(rendered)


def _serialize_runtime_value(value: Any) -> str:
    if value in (None, "", [], {}):
        return ""
    if isinstance(value, str):
        return str(value).strip()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value).strip()


def build_ticket_plan_runtime_context(
    runtime_context: TicketPlanRuntimePayload | None = None,
) -> str:
    payload = runtime_context or TicketPlanRuntimePayload()
    rendered = format_context_sections(
        {
            "规划模式": _serialize_runtime_value(payload.planning_mode),
            "任务目标": _serialize_runtime_value(payload.current_goal),
            "已确定槽位": _serialize_runtime_value(payload.slots),
            "最近失败步骤": _serialize_runtime_value(payload.failed_step),
        }
    )
    return rendered or "[None]"


def build_ticket_execute_runtime_context(
    runtime_context: TicketExecuteRuntimePayload | None = None,
) -> str:
    payload = runtime_context or TicketExecuteRuntimePayload()
    rendered = format_context_sections(
        {
            "总目标": _serialize_runtime_value(payload.goal),
            "当前步骤": _serialize_runtime_value(payload.step),
            "已确定槽位": _serialize_runtime_value(payload.slots),
            "预期收集槽位": _serialize_runtime_value(payload.expected_slots),
        }
    )
    return rendered or "[None]"


def build_post_process_runtime_context(
    runtime_context: PostProcessRuntimePayload | None = None,
) -> str:
    payload = runtime_context or PostProcessRuntimePayload()
    rendered = format_context_sections(
        {
            "当前意图": str(payload.intent or "").strip(),
            "当前判断原因": str(payload.reason or "").strip(),
            "最终状态": str(payload.final_status or "").strip(),
            "最终答复": str(payload.final_reply or "").strip(),
            "执行轨迹": str(payload.trace or "").strip(),
            "关键事实": str(payload.facts or "").strip(),
        }
    )
    return rendered or "[None]"


def build_user_facts_runtime_context(
    runtime_context: UserFactsRuntimePayload | None = None,
) -> str:
    payload = runtime_context or UserFactsRuntimePayload()
    rendered = format_context_sections(
        {
            "已有重要长期事实": _normalize_text_list(list(payload.existing_core_facts or [])),
            "本轮服务记忆摘要": str(payload.current_service_memory_summary or "").strip(),
        }
    )
    return rendered or "[None]"


def build_recommend_guard_runtime_context(
    runtime_context: RecommendGuardRuntimePayload | None = None,
) -> str:
    payload = runtime_context or RecommendGuardRuntimePayload()
    context: Dict[str, Any] = {}

    recommend_context = payload.recommend_context or {}
    summary = str(recommend_context.get("summary") or "").strip()
    context["上一次总结结果"] = recommend_context if recommend_context else {"summary": summary} if summary else {}

    context["上一轮推荐trace"] = payload.last_trace or {}

    compacted = {key: value for key, value in context.items() if value}
    if not compacted:
        return "[None]"
    return json.dumps(compacted, ensure_ascii=False, indent=2, default=str)


def build_recommend_runtime_context(
    runtime_context: Dict[str, Any] | None = None,
) -> str:
    context = runtime_context or {}
    if not context:
        return "{}"
    return json.dumps(context, ensure_ascii=False, indent=2, default=str)


async def build_router_system_prompt(
    *,
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    return build_base_system_prompt(
        prompt_file="router/router.txt",
        capability_context=rendered_capability_context,
    )


async def build_qa_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    return build_base_system_prompt(
        prompt_file="qa/qa.txt",
        user_context=rendered_user_context,
    )


async def build_recommend_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    runtime_context: Dict[str, Any] | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_runtime_context = build_recommend_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="recommend/recommend.txt",
        user_context=rendered_user_context,
        recommend_context=rendered_runtime_context,
    )


async def build_recommend_guard_system_prompt(
    *,
    runtime_context: RecommendGuardRuntimePayload | None = None,
) -> str:
    rendered_runtime_context = build_recommend_guard_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="recommend/guard.txt",
        runtime_context=rendered_runtime_context,
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

async def build_ticket_plan_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    capability_context: PromptCapabilityContext | None = None,
    runtime_context: TicketPlanRuntimePayload | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    rendered_runtime_context = build_ticket_plan_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="ticket/plan.txt",
        user_context=rendered_user_context,
        capability_context=rendered_capability_context,
        runtime_context=rendered_runtime_context,
    )


async def build_ticket_execute_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    capability_context: PromptCapabilityContext | None = None,
    runtime_context: TicketExecuteRuntimePayload | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    rendered_runtime_context = build_ticket_execute_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="ticket/execute.txt",
        user_context=rendered_user_context,
        capability_context=rendered_capability_context,
        runtime_context=rendered_runtime_context,
    )


async def build_post_process_system_prompt(
    *,
    user_context: Mapping[str, Any] | None = None,
    runtime_context: PostProcessRuntimePayload | None = None,
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_user_context = format_user_context(
        _build_prompt_user_context(
            user_context=user_context,
        )
    )
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    rendered_runtime_context = build_post_process_runtime_context(runtime_context)
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
    runtime_context: UserFactsRuntimePayload | None = None,
) -> str:
    rendered_runtime_context = build_user_facts_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="post_process/user_facts.txt",
        runtime_context=rendered_runtime_context,
    )


async def build_ticket_finalize_system_prompt(*, context: str) -> str:
    return build_base_system_prompt(
        prompt_file="ticket/finalize.txt",
        context=str(context or "").strip() or "[None]",
    )
