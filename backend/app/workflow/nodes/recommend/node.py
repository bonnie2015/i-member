from typing import Any, Dict

from app.workflow.state import AgentState


async def recommend_node(_state: AgentState) -> Dict[str, Any]:
    # TODO: ReAct 子图
    return {"final_reply": "我先帮您看看更合适的推荐，马上给您整理。"}
