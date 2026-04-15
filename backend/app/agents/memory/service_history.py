"""
service_history.py — 服务历史缓存

按用户存储最近完整的服务对话，保留最近 N 条（默认5），
超出部分用 LLM 做 summary 后压缩存储。

存储结构（Redis）：
  key: svc:hist:{user_id}        → list of JSON，每条为一次完整服务
  key: svc:summary:{user_id}     → string，较旧服务的 LLM 摘要

每条服务：
  {"intent": "ticket", "messages": [...], "summary": null, "created_at": "..."}
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.config.logging import get_logger
from app.agents.memory.redis_keys import service_history_key, service_summary_key

logger = get_logger("service_history")
_SERVICE_HISTORY_KEEP = 3

def _build_service_summary(messages: List[Dict[str, Any]], final_reply: Optional[str]) -> str:
    user_lines = [str(msg.get("content", "")).strip() for msg in messages if msg.get("role") == "user"]
    entry = user_lines[0] if user_lines else ""
    latest = user_lines[-1] if user_lines else ""
    reply = str(final_reply or "").strip()

    parts = []
    if entry:
        parts.append(f"用户诉求：{entry[:60]}")
    if latest and latest != entry:
        parts.append(f"补充信息：{latest[:60]}")
    if reply:
        parts.append(f"处理结果：{reply[:80]}")

    return "；".join(parts)[:240]


async def _get_redis():
    try:
        from redis.asyncio import Redis as AsyncRedis
        client = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
        return client
    except Exception:
        return None


async def save_service(
    user_id: str,
    intent: str,
    messages: list,
    final_reply: Optional[str] = None,
    merge_with_last: bool = False,
    service_summary_structured: Optional[Dict[str, Any]] = None,
    state_snapshot: Optional[Dict[str, Any]] = None,
) -> None:
    """
    保存一次完整服务到历史缓存。
    超出当前文件定义的保留条数时，把最旧的服务做 summary 后合并存储。
    """
    redis = await _get_redis()
    if not redis:
        return

    try:
        key = service_history_key(user_id)

        # 序列化消息（只保留 role + content）
        msg_list = []
        for msg in messages:
            role = "user" if msg.__class__.__name__ == "HumanMessage" else "assistant"
            msg_list.append({"role": role, "content": msg.content})

        entry = {
            "intent": intent,
            "messages": msg_list,
            "final_reply": final_reply,
            "service_summary": _build_service_summary(msg_list, final_reply),
            "service_summary_structured": service_summary_structured or {},
            "state_snapshot": state_snapshot or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if merge_with_last:
            last_raw = await redis.lindex(key, -1)
            if last_raw:
                try:
                    last_entry = json.loads(last_raw)
                except Exception:
                    last_entry = {}

                merged_messages = (last_entry.get("messages") or []) + msg_list
                merged_entry = {
                    "intent": last_entry.get("intent") or intent,
                    "messages": merged_messages,
                    "final_reply": final_reply,
                    "service_summary": _build_service_summary(merged_messages, final_reply),
                    "service_summary_structured": service_summary_structured or last_entry.get("service_summary_structured") or {},
                    "state_snapshot": state_snapshot or last_entry.get("state_snapshot") or {},
                    "created_at": last_entry.get("created_at") or entry["created_at"],
                    "updated_at": entry["created_at"],
                }
                await redis.lset(key, -1, json.dumps(merged_entry, ensure_ascii=False))
            else:
                await redis.rpush(key, json.dumps(entry, ensure_ascii=False))
        else:
            await redis.rpush(key, json.dumps(entry, ensure_ascii=False))

        # 超出保留条数时，弹出最旧的并做 summary
        total = await redis.llen(key)
        keep = _SERVICE_HISTORY_KEEP
        while total > keep:
            oldest_raw = await redis.lpop(key)
            if oldest_raw:
                try:
                    oldest = json.loads(oldest_raw)
                    await _merge_to_summary(redis, user_id, oldest)
                except Exception:
                    pass
            total -= 1

        logger.info(f"[service_history] saved for {user_id}, total={min(total, keep)}")
    except Exception as e:
        logger.warning(f"[service_history] save failed: {e}")
    finally:
        await redis.aclose()


async def _merge_to_summary(redis, user_id: str, service: Dict[str, Any]) -> None:
    """将一条旧服务记录追加到 summary。"""
    summary_key = service_summary_key(user_id)
    existing_summary = await redis.get(summary_key) or ""

    # 用 LLM 生成摘要片段
    snippet = await _summarize_service(service)
    if not snippet:
        return

    new_summary = f"{existing_summary}\n- {snippet}".strip()
    # 限制 summary 长度（最多保留 2000 字符）
    if len(new_summary) > 2000:
        new_summary = new_summary[-2000:]

    await redis.set(summary_key, new_summary)


async def _summarize_service(service: Dict[str, Any]) -> str:
    """用 LLM 对一次服务做单行摘要。"""
    try:
        from langchain_core.messages import HumanMessage
        from app.agents.llm.llm_factory import get_remote_llm
        from app.agents.prompts.prompt_loader import load_prompt

        msgs = service.get("messages", [])
        conversation = "\n".join(
            f"{'用户' if m['role'] == 'user' else '客服'}：{m['content']}"
            for m in msgs
        )
        if not conversation.strip():
            return ""

        llm = get_remote_llm(role="postprocess")
        prompt = load_prompt("post_process/service_summary.txt").format(conversation=conversation)
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        return resp.content.strip()
    except Exception as e:
        logger.warning(f"[service_history] summarize failed: {e}")
        return ""


async def get_recent_services(user_id: str) -> List[Dict[str, Any]]:
    """获取用户最近的服务历史（含 summary）。"""
    redis = await _get_redis()
    if not redis:
        return []
    try:
        key = service_history_key(user_id)
        raw_list = await redis.lrange(key, 0, -1)
        services = []
        for raw in raw_list:
            try:
                services.append(json.loads(raw))
            except Exception:
                pass

        summary_key = service_summary_key(user_id)
        summary = await redis.get(summary_key)
        if summary:
            services.insert(0, {"type": "summary", "content": summary})

        return services
    except Exception as e:
        logger.warning(f"[service_history] get failed: {e}")
        return []
    finally:
        await redis.aclose()
