from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph

from app.workflow.nodes.ticket.executor import executor_node
from app.workflow.nodes.ticket.guard import guard_node
from app.workflow.nodes.ticket.thinker import thinker_node
from app.workflow.state import AgentState


TicketRoute = Literal["thinker", "executor", "end"]

def _route_after_guard(state: AgentState) -> TicketRoute:
    if str(state.get("service_key") or "").strip():
        return "thinker"
    return "end"


def _route_after_thinker(state: AgentState) -> TicketRoute:
    final_status = str(state.get("final_status") or "").strip()
    if final_status:
        return "end"
    return "executor"


def _route_after_executor(state: AgentState) -> TicketRoute:
    final_status = str(state.get("final_status") or "").strip()
    if final_status:
        return "end"
    return "thinker"


def get_ticket_workflow():
    graph = StateGraph(AgentState)

    graph.add_node("guard", guard_node)
    graph.add_node("thinker", thinker_node)
    graph.add_node("executor", executor_node)

    graph.set_entry_point("guard")
    graph.add_conditional_edges(
        "guard",
        _route_after_guard,
        {
            "thinker": "thinker",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "thinker",
        _route_after_thinker,
        {
            "executor": "executor",
            "end": END,
        },
    )
    graph.add_conditional_edges(
        "executor",
        _route_after_executor,
        {
            "thinker": "thinker",
            "end": END,
        },
    )
    return graph.compile()
