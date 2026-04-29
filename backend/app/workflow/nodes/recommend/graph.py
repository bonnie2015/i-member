from typing import Dict, Any

from langgraph.graph import END, StateGraph

from app.config.logging import get_logger
from app.workflow.state import AgentState
from app.workflow.nodes.recommend.guard import guard_node
from app.workflow.nodes.recommend.recommend import recommend_node

logger = get_logger("recommend_graph")


def _should_continue(state: AgentState) -> str:
    current_subgraph = state.get("current_subgraph")
    if current_subgraph == "recommend":
        return "continue"
    return "end"


def get_recommend_workflow():
    graph = StateGraph(AgentState)

    graph.add_node("guard_node", guard_node)
    graph.add_node("recommend", recommend_node)

    graph.set_entry_point("guard_node")

    graph.add_conditional_edges(
        "guard_node",
        _should_continue,
        {
            "continue": "recommend",
            "end": END,
        },
    )
    graph.add_edge("recommend", END)

    return graph.compile()
