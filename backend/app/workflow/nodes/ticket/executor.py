from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage

from app.agents.ticket.execute_agent import _resolve_step_tools
from app.config.logging import get_logger
from app.llm.llm_factory import get_llm
from app.llm.runtime import estimate_tokens, with_usage_logging
from app.prompts.prompt_builder import (
    TicketExecuteRuntimePayload,
    build_ticket_execute_system_prompt,
)
from app.tools import ask_user_tool, finish_step_tool
from app.tools.business.execution_context import (
    push_ticket_interaction_source,
    ticket_interaction_source_context,
)
from app.config.constants import TICKET_EXECUTOR_MAX_TOOL_CALLS, TRY_PROCESS_MAX_TOKENS
from app.tools.user_interaction_tools import _normalize_interaction
from app.utils.message_utils import message_text
from app.workflow.state import AgentState

logger = get_logger("ticket_executor_node")

# ---------------- try_process helpers ----------------


def _maybe_compress_try_process(tp: list, step_goal: str) -> list:
    """如果 try_process 超过 token 阈值，把旧条目压缩成一条摘要。保留最近 1 对 request+result。"""
    text = json.dumps(tp, ensure_ascii=False, default=str)
    token_count = estimate_tokens(text)
    if token_count < TRY_PROCESS_MAX_TOKENS:
        return tp

    # 找到最近一对 request+result 的起始位置，按 call_id 匹配
    split_at = 0
    for i in range(len(tp) - 1, -1, -1):
        entry = tp[i]
        if "result" in entry:
            call_id = entry.get("call_id")
            for j in range(i - 1, -1, -1):
                if "args" in tp[j] and tp[j].get("call_id") == call_id:
                    split_at = j
                    break
            if split_at > 0:
                break
        elif "args" in entry and "result" not in entry:
            split_at = i
            break

    if split_at <= 0:
        return tp

    old_entries = tp[:split_at]
    recent_entries = tp[split_at:]

    lines: List[str] = []
    for entry in old_entries:
        if "compressed" in entry:
            lines.append(str(entry["compressed"]))
        elif "args" in entry:
            lines.append(
                f"调用了 {entry.get('tool', '')}({json.dumps(entry.get('args', {}), ensure_ascii=False)})"
            )
        elif "result" in entry:
            result = entry.get("result", "")
            result_text = (
                message_text(result) if not isinstance(result, str) else result
            )[:200]
            lines.append(f"结果: {result_text}")

    compressed_text = (
        "【前序操作摘要】步骤目标: " + step_goal[:100] + "\n" + "\n".join(lines)
    )

    logger.info(
        "[executor_node] compressing try_process: %d entries → 1 summary (%d chars → %d tokens)",
        len(old_entries),
        len("".join(lines)),
        estimate_tokens(compressed_text),
    )

    return [{"compressed": compressed_text}, *recent_entries]


def _try_process_to_messages(system_prompt: str, tp: list) -> list:
    """把 try_process 还原为 LLM 对话消息。用 try_process 下标保证 tool_call_id 配对。"""
    messages: list = [SystemMessage(content=system_prompt)]
    pending_tool_call_id: str | None = None

    for i, entry in enumerate(tp):
        if "compressed" in entry:
            messages.append(SystemMessage(content=str(entry["compressed"])))
        elif "args" in entry:
            tool_name = entry.get("tool", "")
            tool_args = entry.get("args", {})
            call_id = entry.get("call_id") or f"call_{i}"
            pending_tool_call_id = call_id
            tool_call = {"name": tool_name, "args": tool_args, "id": call_id}
            messages.append(AIMessage(content="", tool_calls=[tool_call]))
        elif "result" in entry:
            tool_name = entry.get("tool", "")
            result = entry.get("result", "")
            result_text = (
                message_text(result) if not isinstance(result, str) else result
            )
            call_id = entry.get("call_id") or pending_tool_call_id or f"call_{i}"
            pending_tool_call_id = None
            messages.append(
                ToolMessage(content=result_text, name=tool_name, tool_call_id=call_id)
            )

    return messages


# ---------------- ask_user payload builder ----------------


def _build_ask_user_payload(tool_args: dict) -> dict:
    reply = str(tool_args.get("reply") or "").strip()
    interaction_type = tool_args.get("interaction_type")
    candidate_keys = tool_args.get("candidate_keys")
    normalized = _normalize_interaction(interaction_type, candidate_keys=candidate_keys)
    return {"reply": reply, "interaction": normalized}


# ---------------- tool execution ----------------


async def _execute_tool_safe(tool: Any, args: dict) -> Any:
    try:
        if asyncio.iscoroutinefunction(getattr(tool, "ainvoke", None)):
            result = await tool.ainvoke(args)
        elif asyncio.iscoroutinefunction(getattr(tool, "func", None)):
            result = await tool.func(**args)
        elif callable(getattr(tool, "invoke", None)):
            result = tool.invoke(args)
        elif callable(getattr(tool, "func", None)):
            result = tool.func(**args)
        else:
            result = str(tool)
        return result
    except Exception:
        raise


# ---------------- main node ----------------


async def executor_node(state: AgentState) -> Dict[str, Any]:
    steps = list(state.get("steps") or [])
    thread_id = str(state.get("thread_id") or "").strip() or "unknown"

    if not steps:
        logger.warning("[executor_node] thread_id=%s no_steps", thread_id)
        return {
            "final_status": "failed",
            "final_reason": "no_executable_plan",
            "current_step_index": 0,
        }

    current_step_index = int(state.get("current_step_index") or 0)
    if current_step_index >= len(steps):
        logger.warning(
            "[executor_node] thread_id=%s index_out_of_range index=%s total=%s",
            thread_id,
            current_step_index,
            len(steps),
        )
        return {"current_step_index": current_step_index}

    step = dict(steps[current_step_index])
    existing_slots = dict(state.get("slots") or {})
    expected_slots = list(state.get("expected_slots") or [])
    try_process: list = list(step.get("try_process") or [])

    logger.info(
        "[executor_node] thread_id=%s step_index=%s goal=%s try_process_len=%s",
        thread_id,
        current_step_index,
        str(step.get("goal") or "")[:60],
        len(try_process),
    )

    # --- tools & prompt ---
    tools = _resolve_step_tools(step)
    tool_map = {
        str(t.name): t for t in tools if str(t.name) not in ("ask_user", "finish_step")
    }

    prompt_step = {k: v for k, v in step.items() if k != "try_process"}
    system_prompt = await build_ticket_execute_system_prompt(
        runtime_context=TicketExecuteRuntimePayload(
            step=prompt_step,
            slots=existing_slots,
            expected_slots=expected_slots,
        ),
    )

    tool_call_count = sum(1 for e in try_process if "args" in e)
    try_process = _maybe_compress_try_process(try_process, str(step.get("goal") or ""))
    messages = _try_process_to_messages(system_prompt, try_process)

    with ticket_interaction_source_context():
        result = await _run_executor_loop(
            state=state,
            step=step,
            steps=steps,
            step_idx=current_step_index,
            existing_slots=existing_slots,
            try_process=try_process,
            tools=tools,
            tool_map=tool_map,
            messages=messages,
            tool_call_count=tool_call_count,
            thread_id=thread_id,
        )
    return result


async def _run_executor_loop(
    *,
    state: AgentState,
    step: Dict[str, Any],
    steps: list,
    step_idx: int,
    existing_slots: Dict[str, Any],
    try_process: list,
    tools: list,
    tool_map: Dict[str, Any],
    messages: list,
    tool_call_count: int,
    thread_id: str,
) -> Dict[str, Any]:
    for i in range(TICKET_EXECUTOR_MAX_TOOL_CALLS - tool_call_count):
        # --- model ---
        if tool_call_count + i >= TICKET_EXECUTOR_MAX_TOOL_CALLS - 1:
            available_tools = [finish_step_tool, ask_user_tool]
            messages.append(
                SystemMessage(
                    content=(
                        f"已达到工具调用上限（{TICKET_EXECUTOR_MAX_TOOL_CALLS} 次）。必须调用 finish_step 结束本步骤，step_status 设为 pending。"
                    )
                )
            )
        else:
            available_tools = tools

        model = get_llm("ticket")
        model = model.bind_tools(available_tools, tool_choice="required")
        model = with_usage_logging(
            model,
            node="ticket_executor",
            thread_id=thread_id,
            user_id=state.get("user_id"),
            provider="deepseek",
        )

        # --- call ---
        try:
            response = await asyncio.wait_for(model.ainvoke(messages), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("[executor_node] thread_id=%s llm_timeout", thread_id)
            step["step_status"] = "failed"
            step["failed_reason"] = "llm_timeout"
            step["failed_type"] = "system"
            steps[step_idx] = step
            return {
                "steps": steps,
                "slots": existing_slots,
                "current_step_index": step_idx,
            }

        messages.append(response)

        tool_calls = getattr(response, "tool_calls", None) or []
        if not tool_calls:
            continue

        # check for ask_user / finish_step first
        ask_user_tc = next(
            (dict(tc) for tc in tool_calls if str(tc.get("name") or "") == "ask_user"),
            None,
        )
        finish_tc = next(
            (
                dict(tc)
                for tc in tool_calls
                if str(tc.get("name") or "") == "finish_step"
            ),
            None,
        )

        # === ask_user ===
        if ask_user_tc:
            tool_args = dict(ask_user_tc.get("args") or {})
            payload = _build_ask_user_payload(tool_args)
            try_process.append(
                {"tool": "ask_user", "args": tool_args, "interrupt_payload": payload}
            )
            step["try_process"] = try_process
            steps[step_idx] = step
            logger.info(
                "[executor_node] thread_id=%s ask_user reply=%s interaction_type=%s candidate_keys=%s",
                thread_id,
                str(tool_args.get("reply") or "")[:80],
                tool_args.get("interaction_type"),
                tool_args.get("candidate_keys"),
            )
            return {
                "steps": steps,
                "slots": existing_slots,
                "current_step_index": step_idx,
            }

        # === finish_step ===
        if finish_tc:
            tool_args = dict(finish_tc.get("args") or {})
            try_process.append({"tool": "finish_step", "args": tool_args})
            step["try_process"] = try_process
            step["step_status"] = str(tool_args.get("step_status") or "done").strip()
            reason = str(tool_args.get("reason") or "").strip()
            if reason:
                step["failed_reason"] = reason
            new_slots = dict(tool_args.get("slots") or {})
            merged_slots = {**existing_slots, **new_slots}
            logger.info(
                "[executor_node] thread_id=%s finish_step status=%s new_slots=%s",
                thread_id,
                step["step_status"],
                len(new_slots),
            )
            steps[step_idx] = step
            response_dict: Dict[str, Any] = {
                "steps": steps,
                "slots": merged_slots,
                "current_step_index": step_idx,
            }
            reply_text = str(tool_args.get("reply") or "").strip()
            if reply_text:
                response_dict["messages"] = [
                    *state["messages"],
                    AIMessage(content=reply_text),
                ]
            return response_dict

        # === normal tools: execute all ===
        for tc in tool_calls:
            tool_call = dict(tc)
            tool_name = str(tool_call.get("name") or "")
            tool_args = dict(tool_call.get("args") or {})
            logger.info(
                "[executor_node] thread_id=%s tool_call=%s call_%s/%s",
                thread_id,
                tool_name,
                tool_call_count + i + 1,
                TICKET_EXECUTOR_MAX_TOOL_CALLS,
            )

            tool = tool_map.get(tool_name)
            if tool is None:
                call_id = tool_call.get("id", f"call_{tool_call_count + i}")
                error_msg = f"工具 {tool_name} 不在当前步骤可用工具列表中"
                try_process.append(
                    {"tool": tool_name, "args": tool_args, "call_id": call_id}
                )
                try_process.append(
                    {"tool": tool_name, "result": error_msg, "call_id": call_id}
                )
                messages.append(
                    ToolMessage(
                        content=error_msg,
                        tool_call_id=tool_call.get("id", ""),
                        name=tool_name,
                    )
                )
                continue

            try:
                result = await _execute_tool_safe(tool, tool_args)
            except Exception as exc:
                logger.exception(
                    "[executor_node] thread_id=%s tool_error tool=%s: %s",
                    thread_id,
                    tool_name,
                    exc,
                )
                result = f"工具执行错误: {exc}"

            call_id = tool_call.get("id", f"call_{tool_call_count + i}")
            try_process.append(
                {"tool": tool_name, "args": tool_args, "call_id": call_id}
            )
            try_process.append(
                {"tool": tool_name, "result": result, "call_id": call_id}
            )
            if isinstance(result, dict) and "error" in result:
                logger.warning(
                    "[executor_node] thread_id=%s tool_error tool=%s args=%s error=%s",
                    thread_id,
                    tool_name,
                    json.dumps(tool_args, ensure_ascii=False, default=str)[:200],
                    str(result.get("error") or "")[:300],
                )
            result_text = (
                message_text(result) if not isinstance(result, str) else result
            )
            messages.append(
                ToolMessage(
                    content=result_text,
                    tool_call_id=call_id,
                    name=tool_name,
                )
            )

            if isinstance(result, dict):
                push_ticket_interaction_source(result)

        try_process = _maybe_compress_try_process(
            try_process, str(step.get("goal") or "")
        )

    # --- exceeded max tool calls ---
    logger.warning("[executor_node] thread_id=%s exceeded_max_tool_calls", thread_id)
    step["try_process"] = try_process
    step["step_status"] = "pending"
    step["failed_reason"] = f"exceeded_max_tool_calls({TICKET_EXECUTOR_MAX_TOOL_CALLS})"
    steps[step_idx] = step
    return {"steps": steps, "slots": existing_slots, "current_step_index": step_idx}
