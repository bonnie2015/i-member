from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.workflow.nodes.ticket.guard import guard_node
from app.workflow.nodes.ticket.executor import executor_node
from app.workflow.nodes.ticket.planner import plan_node
from app.workflow.nodes.ticket.reflect import reflect_node
from app.workflow.state import TicketNextAction, TicketState


def _action_value(value: object, default: TicketNextAction) -> str:
    if isinstance(value, TicketNextAction):
        return value.value
    text = str(value or "").strip()
    return text or default.value


def _route_after_guard(state: TicketState) -> str:
    if str(state.get("service_key") or "").strip():
        return "plan"
    return "end"


def _route_after_plan(state: TicketState) -> str:
    return _action_value(state.get("next_action"), TicketNextAction.END)


def _route_after_executor(state: TicketState) -> str:
    return _action_value(state.get("next_action"), TicketNextAction.REFLECT)


def _route_after_reflect(state: TicketState) -> str:
    return _action_value(state.get("next_action"), TicketNextAction.END)


def get_ticket_workflow():
    graph = StateGraph(TicketState)

    graph.add_node("guard", guard_node)
    graph.add_node("plan", plan_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflect", reflect_node)

    graph.set_entry_point("guard")
    graph.add_conditional_edges(
        "guard",
        _route_after_guard,
        {
            "plan": "plan",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "plan",
        _route_after_plan,
        {
            "executor": "executor",
            "end": "reflect",
        },
    )
    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {
            "reflect": "reflect",
            "end": "reflect",
        },
    )
    graph.add_conditional_edges(
        "reflect",
        _route_after_reflect,
        {
            "plan": "plan",
            "executor": "executor",
            "end": END,
        },
    )
    return graph.compile()
