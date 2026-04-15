from langgraph.graph import END, StateGraph

from app.config.logging import get_logger
from app.workflow.state import TicketNextAction, TicketState
from app.workflow.nodes.ticket.executor import executor_node
from app.workflow.nodes.ticket.finalizer import finalizer_node
from app.workflow.nodes.ticket.planner import plan_node
from app.workflow.nodes.ticket.reflect import reflect_node

logger = get_logger("ticket_graph")


def after_plan(state: TicketState) -> str:
    return (state.get("next_action") or TicketNextAction.FINALIZE).value


def after_executor(state: TicketState) -> str:
    return (state.get("next_action") or TicketNextAction.REFLECT).value


def after_reflect(state: TicketState) -> str:
    return (state.get("next_action") or TicketNextAction.FINALIZE).value


def get_ticket_workflow():
    graph = StateGraph(TicketState)
    graph.add_node("plan", plan_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflect", reflect_node)
    graph.add_node("finalize", finalizer_node)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", after_plan, {
        "executor": "executor",
        "finalize": "finalize",
    })
    graph.add_conditional_edges("executor", after_executor, {
        "reflect": "reflect",
        "finalize": "finalize",
    })
    graph.add_conditional_edges("reflect", after_reflect, {
        "plan": "plan",
        "executor": "executor",
        "finalize": "finalize",
    })
    graph.add_edge("finalize", END)
    return graph.compile()
