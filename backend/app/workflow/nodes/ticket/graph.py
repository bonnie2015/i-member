from langgraph.graph import END, StateGraph

from app.workflow.state import TicketState
from app.workflow.nodes.ticket.react_agent import ticket_react_agent_node
from app.workflow.nodes.ticket.scene_guard import scene_guard_node


def _scene_guard_route(state: TicketState) -> str:
    if state.get("final_status") or state.get("final_reply"):
        return "end"
    return "react_agent"


def get_ticket_workflow():
    graph = StateGraph(TicketState)
    graph.add_node("scene_guard", scene_guard_node)
    graph.add_node("react_agent", ticket_react_agent_node)

    graph.set_entry_point("scene_guard")
    graph.add_conditional_edges(
        "scene_guard",
        _scene_guard_route,
        {
            "react_agent": "react_agent",
            "end": END,
        },
    )
    graph.add_edge("react_agent", END)
    return graph.compile()
