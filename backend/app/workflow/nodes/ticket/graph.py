from langgraph.graph import END, StateGraph

from app.config.logging import get_logger
from app.workflow.state import TicketNextAction, TicketState
from app.workflow.nodes.ticket.executor import executor_node
from app.workflow.nodes.ticket.planner import plan_node
from app.workflow.nodes.ticket.reflect import reflect_node
from app.workflow.nodes.ticket.scene_guard import scene_guard_node

logger = get_logger("ticket_graph")


def after_plan(state: TicketState) -> str:
    return (state.get("next_action") or TicketNextAction.END).value


def after_executor(state: TicketState) -> str:
    return (state.get("next_action") or TicketNextAction.REFLECT).value


def after_reflect(state: TicketState) -> str:
    return (state.get("next_action") or TicketNextAction.END).value


def get_ticket_workflow():
    graph = StateGraph(TicketState)
    graph.add_node("scene_guard", scene_guard_node)
    graph.add_node("plan", plan_node)
    graph.add_node("executor", executor_node)
    graph.add_node("reflect", reflect_node)

    graph.set_entry_point("scene_guard")
    graph.add_conditional_edges("scene_guard", after_plan, {
        "plan": "plan",
        "end": "reflect",
    })
    graph.add_conditional_edges("plan", after_plan, {
        "executor": "executor",
        "end": "reflect",
    })
    graph.add_conditional_edges("executor", after_executor, {
        "reflect": "reflect",
        "end": "reflect",
    })
    graph.add_conditional_edges("reflect", after_reflect, {
        "plan": "plan",
        "executor": "executor",
        "end": END,
    })
    return graph.compile()
