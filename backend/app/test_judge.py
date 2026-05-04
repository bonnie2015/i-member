"""Judge Agent 测试系统：模拟用户多轮对话 + 评审工单表现

零生产文件改动。通过 monkey-patch 让中断在测试中透传。
"""

import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphInterrupt, GraphRecursionError
from langgraph.types import Command

from app.agents.base import BaseAgent, AgentOutput, AgentStatus
from app.llm.llm_factory import get_llm
from app.tools.business.execution_context import (
    REQUEST_ACCESS_TOKEN_CTX,
    REQUEST_THREAD_ID_CTX,
    REQUEST_USER_ID_CTX,
)
from app.workflow.nodes.ticket.guard import guard_node
from app.workflow.nodes.ticket.executor import executor_node
from app.workflow.nodes.ticket.planner import plan_node
from app.workflow.nodes.ticket.graph import reflect_node, _route_after_reflect, _route_after_guard, _route_after_plan, finalize_node
from app.workflow.state import AgentState


# ══════════════════════════════════════════════════════════════════════════════
# Monkey-patch: 让 GraphInterrupt 从 BaseAgent.run 透传
# ══════════════════════════════════════════════════════════════════════════════

_original_run = BaseAgent.run

async def _patched_run(self, input):
    try:
        return await asyncio.wait_for(
            self._execute(input),
            timeout=self.config.timeout_seconds,
        )
    except GraphInterrupt:
        raise
    except asyncio.TimeoutError:
        return AgentOutput(
            reply=self.config.fallback_reply,
            status=AgentStatus.TIMEOUT,
            error_detail="timeout",
        )
    except GraphRecursionError:
        return AgentOutput(
            reply=self.config.fallback_reply,
            status=AgentStatus.RECURSION_LIMIT,
            error_detail="recursion_limit",
        )
    except Exception as exc:
        return AgentOutput(
            reply=self.config.fallback_reply,
            status=AgentStatus.FAILED,
            error_detail=str(exc),
        )

BaseAgent.run = _patched_run


# ══════════════════════════════════════════════════════════════════════════════
# 场景定义
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Scenario:
    name: str
    user_id: str
    query: str
    answer_policy: str
    expect: Dict[str, Any] = field(default_factory=dict)


SCENARIOS = [
    Scenario(
        name="退货-已知订单号",
        user_id="judge_test_001",
        query="帮我退一下订单N20260501000088里的MEXICO 66 SD鞋子",
        answer_policy=(
            "你知道订单号 N20260501000088，想退里面的 MEXICO 66 SD 休闲鞋 棕色。"
            "积极配合，提供所需信息（退货原因：尺码偏大）。"
            "如果客服让你确认是否取消，你确认取消。"
        ),
        expect={
            "should_complete": True,
            "slots_should_contain": ["order_id", "product_name"],
            "should_not": ["答非所问", "死循环"],
        },
    ),
    Scenario(
        name="退货-需要选订单",
        user_id="judge_test_002",
        query="我想退货，之前买的东西不太满意",
        answer_policy=(
            "你想退货但记不清具体订单号。"
            "客服列出订单后，你选第一个含鞋子的。"
            "确认退的是鞋子。退货原因：穿着不舒服。"
        ),
        expect={
            "should_complete": True,
            "slots_should_contain": ["order_id", "product_name"],
            "should_not": ["没有列出候选就追问订单号"],
        },
    ),
    Scenario(
        name="退货-中途取消",
        user_id="judge_test_004",
        query="帮我退订单N20260501000088里的西装",
        answer_policy=(
            "一开始你想退 N20260501000088 里的男士休闲西装夹克。"
            "当客服开始确认退货细节时，你说'算了不退了'。"
            "客服确认取消后你确认。"
        ),
        expect={
            "final_status_should_be": "cancelled",
            "should_not": ["强制继续退货"],
        },
    ),
]


# ══════════════════════════════════════════════════════════════════════════════
# 构件 ticket 子图（带 MemorySaver，支持中断/恢复）
# ══════════════════════════════════════════════════════════════════════════════

def build_test_ticket_graph():
    """构件带 MemorySaver 的 ticket 子图。"""
    graph = StateGraph(AgentState)
    graph.add_node("guard", guard_node)
    graph.add_node("plan", plan_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("guard")
    graph.add_conditional_edges("guard", _route_after_guard, {"plan": "plan", "end": "finalize"})
    graph.add_conditional_edges("plan", _route_after_plan, {"executor": "executor", "end": "finalize"})
    graph.add_edge("executor", "reflect")
    graph.add_conditional_edges("reflect", _route_after_reflect, {
        "executor": "executor", "plan": "plan", "finalize": "finalize",
    })
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=MemorySaver())


# ══════════════════════════════════════════════════════════════════════════════
# Answer Agent
# ══════════════════════════════════════════════════════════════════════════════

ANSWER_PROMPT = """你是模拟用户，测试客服系统。严格按设定回复。

【你的行为准则】
{answer_policy}

【回复规则】
- 自然口语化，像微信聊天，20-50字
- 客服给候选列表就选出最符合需求的，说出编号或关键信息
- 客服确认取消就按设定诚实回答
- 客服追问具体信息就按设定回答
- 不主动提供设定外的信息
- 只输出回复内容"""


async def answer_agent(
    scenario: Scenario,
    interrupt_reply: str,
    interaction: Dict[str, Any] | None,
    llm,
) -> str:
    interaction_text = ""
    if interaction:
        items = interaction.get("items") or []
        if items:
            lines = ["客服提供的选项："]
            for item in items:
                key = item.get("key", "")
                label = item.get("label", str(item.get("detail", "")))
                lines.append(f"  [{key}] {label}")
            interaction_text = "\n".join(lines)

    prompt = ANSWER_PROMPT.format(answer_policy=scenario.answer_policy)
    user_prompt = f"客服说：{interrupt_reply}\n\n{interaction_text}\n\n请回复："

    response = await llm.ainvoke([
        SystemMessage(content=prompt),
        HumanMessage(content=user_prompt),
    ])
    return str(response.content).strip()


# ══════════════════════════════════════════════════════════════════════════════
# Judge Agent
# ══════════════════════════════════════════════════════════════════════════════

JUDGE_PROMPT = """你是客服系统评审员。评价以下测试对话。

【场景】{name}
【用户诉求】{query}
【用户行为设定】{policy}
【验收标准】
{expect_json}

【对话记录】
{conversation}

JSON 评分（只输出 JSON，不包裹 markdown）：
{{
  "flow_score": <1-10, 流程顺畅度>,
  "slot_score": <1-10, 槽位正确性>,
  "ask_score": <1-10, 追问合理性>,
  "reply_score": <1-10, 回复质量>,
  "overall_score": <1-10, 综合>,
  "passed": <true/false>,
  "issues": ["问题"],
  "summary": "<一句话>"
}}"""


async def judge_agent(scenario, conversation_log, llm) -> Dict[str, Any]:
    prompt = JUDGE_PROMPT.format(
        name=scenario.name,
        query=scenario.query,
        policy=scenario.answer_policy,
        expect_json=json.dumps(scenario.expect, ensure_ascii=False, indent=2),
        conversation=conversation_log or "(无)",
    )
    response = await llm.ainvoke([
        SystemMessage(content="只输出 JSON。"),
        HumanMessage(content=prompt),
    ])
    text = str(response.content).strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"overall_score": 0, "passed": False, "issues": [f"JSON parse: {text[:200]}"], "summary": "Judge 失败"}


# ══════════════════════════════════════════════════════════════════════════════
# Interrupt helper
# ══════════════════════════════════════════════════════════════════════════════

def _list_interrupts(saved_state: Any) -> list:
    interrupts = list(getattr(saved_state, "interrupts", None) or ())
    if interrupts:
        return interrupts
    collected = []
    for task in getattr(saved_state, "tasks", None) or ():
        collected.extend(list(getattr(task, "interrupts", None) or ()))
    return collected


def _has_pending_interrupt(saved_state: Any) -> bool:
    return bool(saved_state and _list_interrupts(saved_state))


def _get_interrupt_payload(saved_state: Any) -> Dict[str, Any] | None:
    interrupts = _list_interrupts(saved_state)
    if not interrupts:
        return None
    payload = interrupts[-1]
    raw = getattr(payload, "value", payload)
    return raw if isinstance(raw, dict) else {}


# ══════════════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════════════

MAX_TURNS = 10


async def run_scenario(scenario: Scenario, graph, llm) -> Dict[str, Any]:
    thread_id = f"judge-{scenario.user_id}"
    config = {"configurable": {"thread_id": thread_id}}

    REQUEST_USER_ID_CTX.set(scenario.user_id)
    REQUEST_THREAD_ID_CTX.set(thread_id)
    REQUEST_ACCESS_TOKEN_CTX.set("judge-mock-token")

    conversation: List[str] = [f"👤: {scenario.query}"]

    # 初始状态
    state: AgentState = {
        "user_id": scenario.user_id,
        "thread_id": thread_id,
        "channel": "app",
        "messages": [HumanMessage(content=scenario.query)],
        "steps": [],
        "current_step_index": 0,
        "replan_count": 0,
        "slots": {},
        "expected_slots": [],
        "user_context": {"member_level": "黄金会员"},
    }

    # 首次调用
    result = await graph.ainvoke(state, config)

    # 中断/恢复循环
    for turn in range(MAX_TURNS):
        saved = await graph.aget_state(config)
        if not _has_pending_interrupt(saved):
            break

        payload = _get_interrupt_payload(saved)
        if not payload:
            break

        interrupt_reply = str(payload.get("reply") or "")
        interaction = payload.get("interaction")
        conversation.append(f"🤖: {interrupt_reply[:200]}")

        # 生成回答并恢复
        answer = await answer_agent(scenario, interrupt_reply, interaction, llm)
        conversation.append(f"👤: {answer}")

        result = await graph.ainvoke(
            Command(resume=answer, update={"messages": [HumanMessage(content=answer)]}),
            config,
        )

        if turn == MAX_TURNS - 1:
            conversation.append("⚠️ 达到最大轮次")

    # 取最终状态
    final_saved = await graph.aget_state(config)
    final_values = getattr(final_saved, "values", {}) or {}

    # 追加系统最后的回复
    messages = final_values.get("messages") or []
    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and not getattr(msg, "tool_calls", None):
            reply_text = str(getattr(msg, "content", "") or "")[:200]
            if reply_text and reply_text not in "\n".join(conversation):
                conversation.append(f"🤖: {reply_text}")
            break

    conversation_log = "\n".join(conversation)
    verdict = await judge_agent(scenario, conversation_log, llm)
    verdict["scenario"] = scenario.name
    verdict["turns"] = len([l for l in conversation if l.startswith("👤")])
    verdict["final_status"] = final_values.get("final_status", "?")
    verdict["conversation"] = conversation_log
    return verdict


# ══════════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════════

async def main():
    print("=" * 60)
    print("  Judge Agent 测试系统 (ticket 子图 + Monkey-patch)")
    print("=" * 60)

    graph = build_test_ticket_graph()
    llm = get_llm("ticket")

    results = []
    for i, scenario in enumerate(SCENARIOS, 1):
        print(f"\n[{i}/{len(SCENARIOS)}] {scenario.name} ...", end=" ", flush=True)
        verdict = await run_scenario(scenario, graph, llm)
        results.append(verdict)

        score = verdict.get("overall_score", "?")
        passed = "✅" if verdict.get("passed") else "❌"
        status = verdict.get("final_status", "?")
        print(f"{passed} 评分 {score}/10  final={status}")

        issues = verdict.get("issues") or []
        for issue in issues[:3]:
            print(f"     ⚠️  {issue}")

    # 汇总
    print(f"\n{'=' * 60}")
    print(f"  汇总")
    print(f"{'=' * 60}")
    total = len(results)
    passed = sum(1 for r in results if r.get("passed"))
    scores = [r.get("overall_score", 0) for r in results if isinstance(r.get("overall_score"), (int, float))]
    avg = sum(scores) / max(len(scores), 1)

    for r in results:
        icon = "✅" if r.get("passed") else "❌"
        print(f"  {icon} {r['scenario']:20s}  {r.get('overall_score', '?')}/10  final={r.get('final_status', '?')}  {r.get('summary', '')}")

    print(f"\n  通过: {passed}/{total}  |  均分: {avg:.1f}/10")

    if passed < total:
        print(f"\n{'=' * 60}")
        print(f"  失败场景对话详情")
        print(f"{'=' * 60}")
        for r in results:
            if not r.get("passed"):
                print(f"\n── {r['scenario']} ──")
                print(r.get("conversation", "(无)"))

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
