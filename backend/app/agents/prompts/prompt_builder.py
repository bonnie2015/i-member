from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
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


class TicketPlanRuntimePayload(BaseModel):
    current_goal: str = ""
    current_step_index: int = 0
    slots: Dict[str, Any] = Field(default_factory=dict)


class TicketExecuteRuntimePayload(BaseModel):
    step: Dict[str, Any] = Field(default_factory=dict)
    slots: Dict[str, Any] = Field(default_factory=dict)


class UserFactsRuntimePayload(BaseModel):
    existing_facts: list[str] = Field(default_factory=list)


class ServiceSummaryRuntimePayload(BaseModel):
    service_type: str = ""
    final_status: str = ""
    final_reason: str = ""


class RecommendGuardRuntimePayload(BaseModel):
    recommend_context: Dict[str, Any] = Field(default_factory=dict)
    last_trace: Dict[str, Any] = Field(default_factory=dict)


_PROMPT_CAPABILITY_FIELD_LABELS: dict[str, str] = {
    "ticket_skills_snapshot": "工单技能快照",
    "selected_skill_content": "当前选中技能",
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
    remaining_step_count = max(5 - int(payload.current_step_index or 0), 0)
    rendered = format_context_sections(
        {
            "任务目标": _serialize_runtime_value(payload.current_goal),
            "当前步骤序号": _serialize_runtime_value(payload.current_step_index),
            "剩余任务规划步数": _serialize_runtime_value(remaining_step_count),
            "已确定槽位": _serialize_runtime_value(payload.slots),
        }
    )
    return rendered or "[None]"


def build_ticket_execute_runtime_context(
    runtime_context: TicketExecuteRuntimePayload | None = None,
) -> str:
    payload = runtime_context or TicketExecuteRuntimePayload()
    rendered = format_context_sections(
        {
            "已确定槽位": _serialize_runtime_value(payload.slots),
            "当前步骤": _serialize_runtime_value(payload.step),
        }
    )
    return rendered or "[None]"


def build_user_facts_runtime_context(
    runtime_context: UserFactsRuntimePayload | None = None,
) -> str:
    payload = runtime_context or UserFactsRuntimePayload()
    rendered = format_context_sections(
        {
            "已有用户事实": _normalize_text_list(list(payload.existing_facts or [])),
        }
    )
    return rendered or "[None]"


def build_service_summary_runtime_context(
    runtime_context: ServiceSummaryRuntimePayload | None = None,
) -> str:
    payload = runtime_context or ServiceSummaryRuntimePayload()
    rendered = format_context_sections(
        {
            "service_type": _serialize_runtime_value(payload.service_type),
            "final_status": _serialize_runtime_value(payload.final_status),
            "final_reason": _serialize_runtime_value(payload.final_reason),
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
        return "[None]"

    summary = str(context.get("summary") or "").strip()
    anchor_products = context.get("anchor_products") or []
    cursor = context.get("cursor") or {}
    rendered = format_context_sections(
        {
            "当前任务总结": (
                "说明：推荐守卫对当前推荐任务、用户最新目标、有效偏好和关键反馈的压缩总结。\n"
                f"内容：{summary}"
            )
            if summary
            else "",
            "重要锚点商品": (
                "说明：用户明确指代或后续推荐必须参考的商品/关键信息；每项 source 说明来源或用途。\n"
                f"内容：{json.dumps(anchor_products, ensure_ascii=False, indent=2, default=str)}"
            )
            if anchor_products
            else "",
            "上一轮搜索批次": (
                "说明：用户要求继续看、换一批或翻页时使用；cursor 描述上一批搜索来源并用于继续翻页。\n"
                f"内容：{json.dumps(cursor, ensure_ascii=False, indent=2, default=str)}"
            )
            if cursor
            else "",
        }
    )
    return rendered or "[None]"


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
    capability_context: PromptCapabilityContext | None = None,
) -> str:
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    return build_base_system_prompt(
        prompt_file="ticket/guard.txt",
        capability_context=rendered_capability_context,
    )

async def build_ticket_plan_system_prompt(
    *,
    capability_context: PromptCapabilityContext | None = None,
    runtime_context: TicketPlanRuntimePayload | None = None,
) -> str:
    rendered_capability_context = _load_prompt_capability_context(capability_context)
    rendered_runtime_context = build_ticket_plan_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="ticket/plan.txt",
        capability_context=rendered_capability_context,
        runtime_context=rendered_runtime_context,
    )


async def build_ticket_execute_system_prompt(
    *,
    runtime_context: TicketExecuteRuntimePayload | None = None,
) -> str:
    rendered_runtime_context = build_ticket_execute_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="ticket/execute.txt",
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


async def build_service_summary_system_prompt(
    *,
    runtime_context: ServiceSummaryRuntimePayload | None = None,
) -> str:
    rendered_runtime_context = build_service_summary_runtime_context(runtime_context)
    return build_base_system_prompt(
        prompt_file="post_process/service_summary.txt",
        runtime_context=rendered_runtime_context,
    )


async def build_ticket_reflect_plan_failed_system_prompt(*, context: str) -> str:
    return build_base_system_prompt(
        prompt_file="ticket/reflect_plan_failed.txt",
        context=str(context or "").strip() or "[None]",
    )


async def build_ticket_executor_result_system_prompt(*, context: str) -> str:
    return build_base_system_prompt(
        prompt_file="ticket/executor_result.txt",
        context=str(context or "").strip() or "[None]",
    )
