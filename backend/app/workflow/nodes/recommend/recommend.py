from typing import Any, Dict

from langchain_core.messages import AIMessage

from app.workflow.state import AgentState


async def recommend_node(_state: AgentState) -> Dict[str, Any]:
    # TODO: ReAct 子图
    final_reply = "我先帮您看看更合适的推荐，马上给您整理。"
    return {
        "trace": ["识别为推荐服务", "当前推荐子图尚未细化，先返回引导性答复"],
        "final_reply": final_reply,
        "messages": [AIMessage(content=final_reply)],
    }
