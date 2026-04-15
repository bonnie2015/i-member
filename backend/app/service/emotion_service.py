"""
emotion_service.py — 用户情绪评分服务

基于对话消息，使用 ollama 轻量模型打 0-1 情绪分：
  0 = 极负面（愤怒/投诉激烈）
  1 = 极正面（高度满意/赞扬）
"""

import json
from typing import List

from langchain_core.messages import BaseMessage, HumanMessage

from app.config.logging import get_logger
from app.agents.prompts.prompt_loader import load_prompt

logger = get_logger("emotion_service")

_PROMPT_FILE = "post_process/emotion_score.txt"
_STRONG_NEGATIVE_KWS = (
    "太差",
    "太差劲",
    "差劲",
    "无语",
    "气死",
    "生气",
    "投诉",
    "骗人",
    "烂",
    "糟糕",
    "离谱",
    "怎么能",
    "有病",
    "垃圾",
)
_MILD_NEGATIVE_KWS = (
    "不好",
    "不满意",
    "有问题",
    "坏了",
    "破了",
    "破损",
    "质量差",
    "失望",
)


def _strong_negative_hit_count(text: str) -> int:
    return sum(1 for keyword in _STRONG_NEGATIVE_KWS if keyword in text)


def _mild_negative_hit_count(text: str) -> int:
    return sum(1 for keyword in _MILD_NEGATIVE_KWS if keyword in text)


def apply_negative_rule_cap(score: float, messages: List[BaseMessage]) -> float:
    """规则兜底：明显负面时压低分数上限，避免被模型保守性抬高。"""
    user_lines = extract_user_messages(messages)
    if not user_lines:
        return score

    text = "\n".join(user_lines)
    strong_hits = _strong_negative_hit_count(text)
    mild_hits = _mild_negative_hit_count(text)

    if strong_hits >= 2:
        return min(score, 0.2)
    if strong_hits >= 1:
        return min(score, 0.3)
    if mild_hits >= 2:
        return min(score, 0.4)
    return score


def extract_user_messages(messages: List[BaseMessage]) -> List[str]:
    """严格只提取当前服务中的用户消息。"""
    user_lines = []
    for msg in messages:
        if msg.__class__.__name__ == "HumanMessage":
            content = str(getattr(msg, "content", "")).strip()
            if content:
                user_lines.append(content)
    return user_lines


def build_user_conversation(messages: List[BaseMessage]) -> str:
    return "\n".join(f"用户：{line}" for line in extract_user_messages(messages))


def detect_negative_tone(messages: List[BaseMessage]) -> bool:
    """用于过程语气调节，比发券阈值更敏感。"""
    user_lines = extract_user_messages(messages)
    if not user_lines:
        return False

    text = "\n".join(user_lines)
    if _strong_negative_hit_count(text) >= 1:
        return True

    mild_hits = _mild_negative_hit_count(text)
    return mild_hits >= 2


async def score_emotion(messages: List[BaseMessage]) -> float:
    """
    对 messages 中的用户发言进行情绪评分。

    Returns:
        0.0 ~ 1.0 的情绪分，出错时返回 0.5（中性）。
    """
    if not messages:
        return 0.5

    conversation = build_user_conversation(messages)
    if not conversation.strip():
        return 0.5

    try:
        from app.agents.llm.llm_factory import get_local_llm
        llm = get_local_llm(role="router")

        prompt = load_prompt(_PROMPT_FILE).format(conversation=conversation)
        resp = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = resp.content.strip()

        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            data = json.loads(raw[start:end])
            score = float(data.get("score", 0.5))
            score = max(0.0, min(1.0, score))
            score = apply_negative_rule_cap(score, messages)
            logger.info(f"[emotion_service] score={score:.2f}")
            return score
    except Exception as e:
        logger.warning(f"[emotion_service] scoring failed: {e}")

    return 0.5
