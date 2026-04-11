"""
ticket_planer.py — plan 节点

职责：
1. 调用 LLM 分析用户消息，生成工单执行计划（PlanOutput）。
2. 若信息不全则标记 need_more_info，让子图 clarify 节点进行 interrupt 追问。
3. 若识别到非工单意图，设置 has_other_intent=True，子图将触发二次意图识别后
   把结果追加到 intent_queue。
"""

import json
from typing import Any, Dict, List, Optional

from langchain_core.messages import SystemMessage
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.config.logging import get_logger
from app.prompts.prompt_loader import load_prompt
from app.utils.skills_utils import load_skill, load_skills_snapshot
from app.workflow.state import TicketState

logger = get_logger("ticket_planer")

TICKET_PLAN_PROMPT_FILE = "ticket_plan.txt"

MAX_CLARIFY = 5   # 追问上限
MAX_LOOP = 8      # 规划-执行循环上限


# ---------------------------------------------------------------------------
# Pydantic 结构化输出模型
# ---------------------------------------------------------------------------

class PlanStep(BaseModel):
    step: int
    skill: Optional[str] = None
    tool: str
    input: Dict[str, Any]
    purpose: str


class PlanOutput(BaseModel):
    target: str
    need_more_info: bool
    clarify_question: Optional[str] = None
    has_other_intent: bool = False
    steps: List[PlanStep]
    need_confirm: bool


# ---------------------------------------------------------------------------
# plan 节点
# ---------------------------------------------------------------------------

async def plan_node(state: TicketState) -> Dict[str, Any]:
    """
    plan 节点：生成执行计划。

    返回更新字段：
    - plan: 序列化的 PlanOutput
    - has_other_intent: 是否含非工单意图（供子图决定是否触发意图识别）
    - loop_count: +1
    """
    user_id = state.get("user_id", "unknown")
    messages = state.get("messages", [])
    collected_info = state.get("collected_info") or {}
    loop_count = state.get("loop_count", 0)

    # 安全熔断
    if loop_count >= MAX_LOOP:
        logger.warning(f"[plan_node] loop_count={loop_count} >= MAX_LOOP, force end")
        return {
            "plan": None,
            "loop_count": loop_count + 1,
            "force_end": True,
        }

    # 加载 skill 快照
    skills_snapshot = load_skills_snapshot()

    # 如果已有 plan 且引用了具体 skill，则按需加载完整 skill 详情
    existing_plan = state.get("plan")
    if existing_plan and existing_plan.get("steps"):
        involved = {s.get("skill") for s in existing_plan["steps"] if s.get("skill")}
        skill_detail_parts = []
        for skill_name in involved:
            detail = load_skill(skill_name)
            skill_detail_parts.append(detail)
        skills_text = "\n\n".join(skill_detail_parts) if skill_detail_parts else skills_snapshot
    else:
        skills_text = skills_snapshot

    # 构造 system prompt
    collected_info_str = json.dumps(collected_info, ensure_ascii=False) if collected_info else "暂无"
    system_content = load_prompt(TICKET_PLAN_PROMPT_FILE).format(
        user_id=user_id,
        skills=skills_text,
        collected_info=collected_info_str,
    )

    llm = get_remote_llm(role="ticket")
    llm_with_output = llm.with_structured_output(PlanOutput)

    llm_messages = [SystemMessage(content=system_content)] + list(messages)

    try:
        plan: PlanOutput = await llm_with_output.ainvoke(llm_messages)
        logger.info(f"[plan_node] plan generated: target={plan.target}, need_more_info={plan.need_more_info}, has_other_intent={plan.has_other_intent}")
    except Exception as e:
        logger.error(f"[plan_node] LLM error: {e}", exc_info=True)
        return {
            "plan": None,
            "loop_count": loop_count + 1,
            "force_end": True,
        }

    return {
        "plan": plan.model_dump(),
        "has_other_intent": plan.has_other_intent,
        "loop_count": loop_count + 1,
        "current_step": 0,
        "execution_results": [],
    }
