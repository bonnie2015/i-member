import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import interrupt
from pydantic import BaseModel

from app.prompts.prompt_loader import load_prompt
from app.agents.llm.llm_factory import get_remote_llm
from app.utils.skills_utils import load_skills_snapshot, load_skill
from app.config.logging import get_logger
from app.workflow.state import AgentState

logger = get_logger("ticket")

# Prompt file constants
TICKET_PLAN_PROMPT_FILE = "ticket_plan.txt"
TICKET_REFLECT_PROMPT_FILE = "ticket_reflect.txt"


# Pydantic models for structured output
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
    steps: List[PlanStep]
    need_confirm: bool

class ReflectOutput(BaseModel):
    satisfied: bool
    reply: str
    missing: List[str]


# ---------------------------------------------------------------------------
# Mock 工具注册表
# ---------------------------------------------------------------------------

_REGISTRY: Dict[str, Any] = {
    # member-info
    "get_user_tags": lambda i: {"user_id": i["user_id"], "tags": ["高价值", "复购客户"], "tag_details": []},
    "get_user_level": lambda i: {"user_id": i["user_id"], "level": "黄金会员", "level_code": 3, "points": 8500, "points_to_next": 1500, "next_level": "铂金会员"},
    "get_customer_profile": lambda i: {"user_id": i["user_id"], "name": "张三", "member_level": "黄金会员", "points": 8500, "total_orders": 42, "tags": ["高价值"]},
    "get_user_behavior": lambda i: {"user_id": i["user_id"], "total": 1, "events": [{"type": "purchase", "description": "购买商品 SKU-888", "amount": 299.0}]},
    "upgrade_membership": lambda i: {"user_id": i["user_id"], "success": True, "old_level": "黄金会员", "new_level": "铂金会员", "message": "升级成功！"},
    # order
    "get_user_orders": lambda i: {"user_id": i["user_id"], "total": 1, "orders": [{"order_id": "ORD-20241115-001", "status": "shipped", "status_label": "已发货", "amount": 299.0, "items_summary": "深蓝色卫衣 × 1"}]},
    "get_order_detail": lambda i: {"order_id": i["order_id"], "user_id": i["user_id"], "status": "shipped", "amount": 299.0, "items": [{"sku_id": "SKU-888", "name": "深蓝色卫衣", "qty": 1, "price": 299.0}], "logistics_no": "SF1234567890"},
    # logistics
    "get_logistics": lambda i: {"order_id": i["order_id"], "logistics_company": "顺丰速运", "logistics_no": "SF1234567890", "status": "in_transit", "status_label": "运输中", "tracks": [{"time": "2024-11-16 08:30:00", "location": "北京转运中心", "description": "已到达分拣中心"}]},
    # product
    "query_product": lambda i: {"total": 1, "products": [{"sku_id": "SKU-888", "name": "深蓝色卫衣", "price": 299.0, "stock": 152, "status": "on_sale"}]},
    "query_promotion": lambda i: {"promotions": [{"promo_id": "P2024001", "title": "全场8折", "discount_rate": 0.8, "channels": ["app", "wechat", "web"]}]},
    # ticket
    "create_ticket": lambda i: {"ticket_id": "TK-20241120-0042", "status": "open", "ticket_type": i["ticket_type"], "title": i["title"], "estimated_response": "1个工作日内"},
    "get_ticket": lambda i: {"ticket_id": i["ticket_id"], "status": "processing", "status_label": "处理中", "assignee": "客服专员 李华"},
}


async def execute_tool(name: str, input: Dict[str, Any]) -> Dict[str, Any]:
    fn = _REGISTRY.get(name)
    if fn is None:
        return {"error": f"未知工具: {name}"}
    try:
        return fn(input)
    except Exception as e:
        return {"error": str(e)}


class TicketAgent:

    def __init__(self) -> None:
        self._llm = get_remote_llm(role="ticket")
        self._snapshot = load_skills_snapshot()
        self._reflect_prompt = load_prompt(TICKET_REFLECT_PROMPT_FILE)

    async def _plan(
        self,
        messages: List,
        user_id: str,
        skill_names: List[str],
    ) -> PlanOutput:
        """
        生成执行计划
        使用独立的 TicketPlannerAgent
        """
        from app.agents.ticket_planer import get_ticket_planner_agent

        planner_agent = get_ticket_planner_agent()
        return await planner_agent.plan(messages, user_id, skill_names)

    async def _execute(self, plan: PlanOutput) -> List[Dict[str, Any]]:
        results = []
        for step in plan.steps:
            tool_name = step.tool
            tool_input = step.input
            logger.info(f"Execute step {step.step}: {tool_name}({tool_input})")
            result = await execute_tool(tool_name, tool_input)
            results.append({
                "step": step.step,
                "tool": tool_name,
                "purpose": step.purpose,
                "result": result,
            })
        return results

    async def _reflect(self, messages: List, results: List[Dict[str, Any]]) -> ReflectOutput:

        llm_with_structured_output = self._llm.with_structured_output(ReflectOutput)

        results_text = json.dumps(results, ensure_ascii=False, indent=2)
        user_input = messages[-1].content if messages else ""
        llm_messages = (
[HumanMessage(content=self._reflect_prompt.format(user_input=user_input, results=results_text))]
        )
        response = await llm_with_structured_output.ainvoke(llm_messages)
        return response



_agent: Optional[TicketAgent] = None


def get_ticket_agent() -> TicketAgent:
    global _agent
    if _agent is None:
        _agent = TicketAgent()
    return _agent


async def ticket_node(state: AgentState) -> Dict[str, Any]:
    """Ticket 处理节点，支持 interrupt 追问机制。

    interrupt(question) 挂起图并向前端返回追问。
    resume 后节点从头重新执行，interrupt() 返回用户的回答，
    将其追加到本地 messages 列表后继续 plan。
    """
    agent = get_ticket_agent()
    user_id = state.get("user_id", "unknown")
    messages = state.get("messages", [])

    # Plan 循环：最多追问 3 次，防止死循环
    for _ in range(3):
        plan = await agent._plan(messages, user_id, skill_names=[])

        if not plan.need_more_info:
            break

        question = plan.clarify_question or "请问您能提供更多详情吗？"
        user_answer = interrupt(question)
        messages = messages + [HumanMessage(content=user_answer)]

    # 按需加载 skill 详情，精化 plan
    involved_skills = list({s.skill for s in plan.steps if s.skill})
    if involved_skills:
        plan = await agent._plan(messages, user_id, skill_names=involved_skills)
        logger.info(f"[ticket_node] plan-refined={plan}")
        if plan.need_more_info:
            question = plan.clarify_question or "请问您能提供更多详情吗？"
            user_answer = interrupt(question)
            messages = messages + [HumanMessage(content=user_answer)]

    if plan.need_confirm:
        logger.info("[ticket_node] 写操作 demo 自动确认")

    results = await agent._execute(plan)
    reflection = await agent._reflect(messages, results)
    reply = reflection.reply or "抱歉，我无法处理您的请求，请稍后重试。"
    return {"final_reply": reply, "messages": messages}
