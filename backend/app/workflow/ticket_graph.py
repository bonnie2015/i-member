"""
ticket_graph.py — ticket 子图（Plan-and-Execute-Reflect 模式）

流程：
  plan_node
    → [need_more_info?] clarify_node (interrupt 追问, 最多 MAX_CLARIFY 次)
        → plan_node（循环，直到信息充足）
    → [has_other_intent?] other_intent_node（识别后追加 intent_queue）
    → [need_confirm?] confirm_node（interrupt 向用户确认写操作）
    → executor_node（执行单步）
    → reflect_node
        → continue → executor_node
        → retry    → executor_node
        → replan   → plan_node
        → done     → END
        → fail     → END
"""

import json
from typing import Any, Dict, List, Literal, Optional

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel

from app.agents.llm.llm_factory import get_remote_llm
from app.config.config import settings
from app.config.logging import get_logger
from app.prompts.prompt_loader import load_prompt
from app.service.intent_recognition import recognize_intent
from app.workflow.node.ticket_planer import MAX_CLARIFY, MAX_LOOP, plan_node
from app.workflow.state import TicketState

logger = get_logger("ticket_graph")

TICKET_REFLECT_PROMPT_FILE = "ticket_reflect.txt"
MAX_RETRY = 3   # executor 单步最大重试次数


# ---------------------------------------------------------------------------
# 结构化输出模型
# ---------------------------------------------------------------------------

class ReflectOutput(BaseModel):
    action: Literal["continue", "replan", "retry", "done", "fail"]
    satisfied: bool
    reply: Optional[str] = None
    reason: str
    missing: List[str] = []


# ---------------------------------------------------------------------------
# Tool executor（HTTP 调用 SCRM 接口）
# ---------------------------------------------------------------------------

async def _call_scrm(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """通过 HTTP 调用 SCRM 接口。base_url 和 access_token 来自环境变量。
    若 HTTP 调用失败，自动降级到本地 mock 注册表（仅用于本地开发/测试）。
    """
    base_url = getattr(settings, "scrm_base_url", "http://bonnie-local.com")
    access_token = getattr(settings, "scrm_access_token", "")

    url = f"{base_url}/api/{tool_name}"
    headers = {"access_token": access_token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=tool_input, headers=headers)
            resp.raise_for_status()
            return resp.json()
    except Exception:
        # HTTP 不可达时降级到 mock
        return _call_mock(tool_name, tool_input)


# ---------------------------------------------------------------------------
# 本地 Mock 注册表（开发 / 测试用）
# ---------------------------------------------------------------------------

_MOCK_REGISTRY: Dict[str, Any] = {
    "get_user_tags": lambda i: {"user_id": i["user_id"], "tags": ["高价值", "复购客户"], "tag_details": []},
    "get_user_level": lambda i: {"user_id": i["user_id"], "level": "黄金会员", "level_code": 3, "points": 8500, "points_to_next": 1500, "next_level": "铂金会员"},
    "get_customer_profile": lambda i: {"user_id": i["user_id"], "name": "张三", "member_level": "黄金会员", "points": 8500, "total_orders": 42, "tags": ["高价值"]},
    "get_user_behavior": lambda i: {"user_id": i["user_id"], "total": 1, "events": [{"type": "purchase", "description": "购买商品 SKU-888", "amount": 299.0}]},
    "upgrade_membership": lambda i: {"user_id": i["user_id"], "success": True, "old_level": "黄金会员", "new_level": "铂金会员", "message": "升级成功！"},
    "get_user_orders": lambda i: {"user_id": i["user_id"], "total": 1, "orders": [{"order_id": "ORD-20241115-001", "status": "shipped", "status_label": "已发货", "amount": 299.0, "items_summary": "深蓝色卫衣 × 1"}]},
    "get_order_detail": lambda i: {"order_id": i.get("order_id", "ORD-20241115-001"), "user_id": i["user_id"], "status": "shipped", "amount": 299.0, "items": [{"sku_id": "SKU-888", "name": "深蓝色卫衣", "qty": 1, "price": 299.0}], "logistics_no": "SF1234567890"},
    "get_logistics": lambda i: {"order_id": i.get("order_id"), "logistics_company": "顺丰速运", "logistics_no": "SF1234567890", "status": "in_transit", "status_label": "运输中", "tracks": [{"time": "2024-11-16 08:30:00", "location": "北京转运中心", "description": "已到达分拣中心"}]},
    "query_product": lambda i: {"total": 1, "products": [{"sku_id": "SKU-888", "name": "深蓝色卫衣", "price": 299.0, "stock": 152, "status": "on_sale"}]},
    "query_promotion": lambda i: {"promotions": [{"promo_id": "P2024001", "title": "全场8折", "discount_rate": 0.8, "channels": ["app", "wechat", "web"]}]},
    "create_ticket": lambda i: {"ticket_id": "TK-20241120-0042", "status": "open", "ticket_type": i.get("ticket_type", "complaint"), "title": i.get("title", "工单"), "estimated_response": "1个工作日内"},
    "get_ticket": lambda i: {"ticket_id": i.get("ticket_id"), "status": "processing", "status_label": "处理中", "assignee": "客服专员 李华"},
}


def _call_mock(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    fn = _MOCK_REGISTRY.get(tool_name)
    if fn is None:
        return {"error": f"未知工具: {tool_name}"}
    try:
        return fn(tool_input)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# clarify 节点（interrupt 追问）
# ---------------------------------------------------------------------------

async def clarify_node(state: TicketState) -> Dict[str, Any]:
    """
    通过 interrupt 向用户提问，恢复后用 ollama 验收：
    - 提取新信息合并到 collected_info
    - 只检查 required 问题是否全部回答
    - required 全部满足 → 在 collected_info 打 _required_done 标记，plan_node 看到后直接规划
    - required 未满足 → 更新 clarify_items 只保留未答的 required 问题，继续追问
    """
    plan = state.get("plan") or {}
    clarify_items = plan.get("clarify_items") or []
    clarify_count = state.get("clarify_count", 0)

    # 只向用户展示 required 问题（如果有），否则展示全部
    required_items = [i for i in clarify_items if i.get("required", True)]
    display_items = required_items if required_items else clarify_items
    if display_items:
        question = "\n".join(f"{idx+1}. {i['question']}" for idx, i in enumerate(display_items))
    else:
        question = plan.get("clarify_question") or "请问您能提供更多详情吗？"

    logger.info(f"[clarify_node] round={clarify_count + 1}, required_count={len(required_items)}")

    # interrupt 挂起，等待用户回答
    user_answer: str = interrupt(question)

    new_messages = [HumanMessage(content=user_answer)]
    collected_info = dict(state.get("collected_info") or {})

    # Ollama 验收：提取信息 + 检查 required 问题是否回答
    required_questions = [i["question"] for i in clarify_items if i.get("required", True)]
    required_done = False
    unanswered_required: List[str] = []

    try:
        from app.agents.llm.llm_factory import get_local_llm
        llm = get_local_llm(role="router")

        verify_prompt = (
            f"用户回答：{user_answer}\n"
            f"已收集信息：{json.dumps(collected_info, ensure_ascii=False)}\n"
            f"必须回答的问题列表：{json.dumps(required_questions, ensure_ascii=False)}\n\n"
            "请完成两件事：\n"
            "1. 从用户回答中提取信息，合并到已收集信息\n"
            "2. 检查必须回答的问题中，哪些仍未得到回答\n\n"
            "仅输出 JSON，格式：\n"
            '{"collected": {...合并后的完整信息...}, "unanswered_required": ["未回答的必填问题1", ...]}'
        )
        resp = await llm.ainvoke([HumanMessage(content=verify_prompt)])
        raw = resp.content.strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(raw[start:end])
            if isinstance(result.get("collected"), dict):
                collected_info = result["collected"]
            unanswered_required = result.get("unanswered_required") or []
            required_done = len(unanswered_required) == 0
    except Exception as e:
        logger.warning(f"[clarify_node] ollama verification failed: {e}")
        # 降级：假设 required 已满足，避免死循环
        required_done = True

    if required_done:
        # 必填项全部满足，打标记让 plan_node 直接生成执行计划
        collected_info["_required_done"] = True
        logger.info("[clarify_node] all required questions answered, marking _required_done")
    else:
        # 只保留还未回答的 required 问题，更新 plan 的 clarify_items
        logger.info(f"[clarify_node] still missing required: {unanswered_required}")
        updated_items = [{"question": q, "required": True} for q in unanswered_required]
        plan = dict(plan)
        plan["clarify_items"] = updated_items
        plan["clarify_question"] = "\n".join(
            f"{i+1}. {q}" for i, q in enumerate(unanswered_required)
        )
        return {
            "messages": new_messages,
            "collected_info": collected_info,
            "clarify_count": clarify_count + 1,
            "plan": plan,
        }

    return {
        "messages": new_messages,
        "collected_info": collected_info,
        "clarify_count": clarify_count + 1,
    }


# ---------------------------------------------------------------------------
# other_intent 节点（非工单意图识别后追加 intent_queue）
# ---------------------------------------------------------------------------

async def other_intent_node(state: TicketState) -> Dict[str, Any]:
    """
    调用意图识别服务，把非工单意图追加到 intent_queue。
    """
    messages = state.get("messages", [])
    user_input = messages[-1].content if messages else ""

    try:
        result = await recognize_intent(user_input)
        existing_queue: List[str] = state.get("intent_queue") or []
        new_intents = [result.intent] + result.secondary_intents
        # 去重合并，最多3个
        merged = list(dict.fromkeys(existing_queue + new_intents))[:3]
        logger.info(f"[other_intent_node] merged intent_queue={merged}")
        return {"intent_queue": merged}
    except Exception as e:
        logger.warning(f"[other_intent_node] intent recognition error: {e}")
        return {}


# ---------------------------------------------------------------------------
# confirm 节点（interrupt 向用户确认写操作）
# ---------------------------------------------------------------------------

async def confirm_node(state: TicketState) -> Dict[str, Any]:
    """
    对包含写操作的计划向用户二次确认。
    """
    plan = state.get("plan") or {}
    target = plan.get("target", "执行以上操作")
    steps = plan.get("steps", [])
    write_ops = [s["tool"] for s in steps if s["tool"] in ("create_ticket", "upgrade_membership")]

    confirm_msg = (
        f'即将执行：{target}\n涉及写操作：{", ".join(write_ops)}\n'
        '请确认是否继续？（回复"确认"或"取消"）'
    )
    logger.info(f"[confirm_node] asking for confirmation: {confirm_msg}")

    user_answer: str = interrupt(confirm_msg)
    confirmed = "确认" in user_answer or "yes" in user_answer.lower() or "ok" in user_answer.lower()

    if not confirmed:
        logger.info("[confirm_node] user cancelled operation")
        return {
            "final_reply": "操作已取消，如需重新操作请告知。",
            "force_end": True,
        }
    return {"user_confirmed": True}


# ---------------------------------------------------------------------------
# executor 节点（执行单步）
# ---------------------------------------------------------------------------

async def executor_node(state: TicketState) -> Dict[str, Any]:
    """
    执行当前 plan 中的单个步骤，并记录结果。
    """
    plan = state.get("plan") or {}
    steps = plan.get("steps", [])
    current_step = state.get("current_step", 0)
    results: List[Dict[str, Any]] = list(state.get("execution_results") or [])
    retry_count = state.get("retry_count", 0)

    if current_step >= len(steps):
        logger.info("[executor_node] no more steps to execute")
        return {}

    step = steps[current_step]
    tool_name = step["tool"]
    tool_input = dict(step["input"])  # copy to avoid mutating state
    # 注入 user_id
    tool_input.setdefault("user_id", state.get("user_id", "unknown"))

    logger.info(f"[executor_node] step={current_step + 1}, tool={tool_name}, input={tool_input}")

    result = await _call_scrm(tool_name, tool_input)
    logger.info(f"[executor_node] result={result}")

    results.append({
        "step": step.get("step", current_step + 1),
        "tool": tool_name,
        "purpose": step.get("purpose", ""),
        "result": result,
    })

    return {
        "execution_results": results,
        "retry_count": 0,  # 成功后重置 retry_count
    }


# ---------------------------------------------------------------------------
# reflect 节点
# ---------------------------------------------------------------------------

async def reflect_node(state: TicketState) -> Dict[str, Any]:
    """
    反思当前执行状态，决定下一步动作。
    """
    messages = state.get("messages", [])
    plan = state.get("plan") or {}
    results = state.get("execution_results") or []
    current_step = state.get("current_step", 0)
    total_steps = len(plan.get("steps", []))

    user_input = messages[-1].content if messages else ""
    results_text = json.dumps(results, ensure_ascii=False, indent=2)

    # 告知 reflect 当前执行进度，避免它因为"目标未完成"而错误 replan
    progress_note = f"（当前已执行第 {current_step + 1} 步，计划共 {total_steps} 步）"

    reflect_prompt = load_prompt(TICKET_REFLECT_PROMPT_FILE).format(
        user_input=user_input,
        target=plan.get("target", "") + progress_note,
        results=results_text,
    )

    llm = get_remote_llm(role="ticket")
    llm_with_output = llm.with_structured_output(ReflectOutput)

    try:
        reflect: ReflectOutput = await llm_with_output.ainvoke([
            SystemMessage(content=reflect_prompt)
        ])
    except Exception as e:
        logger.error(f"[reflect_node] LLM error: {e}", exc_info=True)
        return {"reflect_action": "fail", "final_reply": "任务执行出现错误，请重试。"}

    logger.info(f"[reflect_node] action={reflect.action}, reason={reflect.reason}")

    updates: Dict[str, Any] = {
        "reflect_action": reflect.action,
        "loop_count": state.get("loop_count", 0) + 1,
    }

    if reflect.action == "continue":
        # 移动到下一步
        updates["current_step"] = current_step + 1
    elif reflect.action == "retry":
        updates["retry_count"] = state.get("retry_count", 0) + 1
    elif reflect.action == "replan":
        pass  # execution_results 会由 plan_node 重置；无需额外操作
    elif reflect.action == "done":
        updates["final_reply"] = reflect.reply or "您的请求已处理完成。"
    elif reflect.action == "fail":
        updates["final_reply"] = reflect.reply or "任务失败，请重试。"

    return updates


# ---------------------------------------------------------------------------
# 条件边函数
# ---------------------------------------------------------------------------

def after_plan(state: TicketState) -> str:
    """
    plan 后路由（单一条件边，覆盖所有分支）：
      force_end → end
      need_more_info & clarify_count < MAX_CLARIFY → clarify
      has_other_intent → other_intent
      need_confirm → confirm
      else → executor
    """
    if state.get("force_end"):
        return "end"

    plan = state.get("plan") or {}
    clarify_count = state.get("clarify_count", 0)

    if plan.get("need_more_info") and clarify_count < MAX_CLARIFY:
        return "clarify"

    if state.get("has_other_intent"):
        return "other_intent"

    if plan.get("need_confirm") and not state.get("user_confirmed"):
        return "confirm"

    return "executor"


def after_clarify(_state: TicketState) -> str:
    """追问后重新 plan。"""
    return "plan"


def after_other_intent(state: TicketState) -> str:
    """其他意图处理完成后，检查是否有写操作需要确认。"""
    plan = state.get("plan") or {}
    if plan.get("need_confirm") and not state.get("user_confirmed"):
        return "confirm"
    return "executor"


def after_confirm(state: TicketState) -> str:
    if state.get("force_end"):
        return "end"
    return "executor"


def after_executor(_state: TicketState) -> str:
    return "reflect"


def after_reflect(state: TicketState) -> str:
    action = state.get("reflect_action", "fail")
    loop_count = state.get("loop_count", 0)

    if loop_count >= MAX_LOOP:
        return "end"

    if action == "continue":
        plan = state.get("plan") or {}
        steps = plan.get("steps", [])
        current_step = state.get("current_step", 0)
        if current_step >= len(steps):
            return "end"
        return "executor"
    elif action == "retry":
        retry_count = state.get("retry_count", 0)
        if retry_count >= MAX_RETRY:
            return "end"
        return "executor"
    elif action == "replan":
        return "plan"
    elif action in ("done", "fail"):
        return "end"
    return "end"


# ---------------------------------------------------------------------------
# 子图组装
# ---------------------------------------------------------------------------

def get_ticket_workflow():
    """构建并返回编译后的 ticket 子图（CompiledGraph）。"""

    graph = StateGraph(TicketState)

    # 节点注册
    graph.add_node("plan", plan_node)
    graph.add_node("clarify", clarify_node)
    graph.add_node("other_intent", other_intent_node)
    graph.add_node("confirm", confirm_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflect", reflect_node)

    # 入口
    graph.set_entry_point("plan")

    # 条件边
    graph.add_conditional_edges("plan", after_plan, {
        "clarify": "clarify",
        "other_intent": "other_intent",
        "confirm": "confirm",
        "executor": "executor",
        "end": END,
    })
    graph.add_conditional_edges("clarify", after_clarify, {
        "plan": "plan",
    })
    graph.add_conditional_edges("other_intent", after_other_intent, {
        "confirm": "confirm",
        "executor": "executor",
    })
    graph.add_conditional_edges("confirm", after_confirm, {
        "executor": "executor",
        "end": END,
    })
    graph.add_conditional_edges("executor", after_executor, {
        "reflect": "reflect",
    })
    graph.add_conditional_edges("reflect", after_reflect, {
        "executor": "executor",
        "plan": "plan",
        "end": END,
    })

    return graph.compile()
