import uuid
from typing import Optional, Dict, Any
from langgraph.graph import StateGraph, END
from langgraph.types import Command
from langchain_core.messages import HumanMessage
from app.workflow.state import AgentState
from app.workflow.node.router import router_node, router_condition
from app.config.logging import get_logger
from app.workflow.ticket_graph import get_ticket_workflow

logger = get_logger("workflow")

workflow = None  # 由 lifespan 初始化


async def qa_agent(state: AgentState):
    reply = "正在为您查询相关政策..."
    logger.info("QA模块 - 处理用户查询")
    return {"final_reply": reply}


async def recommend_agent(state: AgentState):
    # TODO: ReAct 子图
    return {"final_reply": "为您推荐这款商品..."}


async def post_process_node(state: AgentState):
    """
    后处理节点

    对话结束后执行：
    1. 写入长期记忆（服务历史、情绪得分）
    2. 情绪补偿判断（负面+高价值 → 发补偿券）
    3. intent_queue 处理（直接路由到下一子图）
    """

    # TODO: 实现记忆写入
    # TODO: 实现情绪补偿判断

    # 确保 intent_queue 存在
    if "intent_queue" not in state:
        state["intent_queue"] = []

    return {}


def create_workflow(checkpointer):
    workflow_graph = StateGraph(AgentState)

    workflow_graph.add_node("router_node", router_node)

    ticket_agent = get_ticket_workflow()
    workflow_graph.add_node("ticket_agent", ticket_agent)
    workflow_graph.add_node("qa_agent", qa_agent)
    workflow_graph.add_node("recommend_agent", recommend_agent)
    workflow_graph.add_node("post_process_node", post_process_node)

    workflow_graph.set_entry_point("router_node")

    workflow_graph.add_conditional_edges(
        "router_node",
        router_condition,
        {
            "ticket": "ticket_agent",
            "qa": "qa_agent",
            "recommend": "recommend_agent"
        }
    )

    workflow_graph.add_edge("ticket_agent", "post_process_node")
    workflow_graph.add_edge("qa_agent", "post_process_node")
    workflow_graph.add_edge("recommend_agent", "post_process_node")
    workflow_graph.add_edge("post_process_node", END)

    return workflow_graph.compile(checkpointer=checkpointer)


async def run_member_ops_agent(
    user_message: str,
    user_id: str,
    thread_id: Optional[str] = None,
    channel: str = "api"
) -> Dict[str, Any]:

    try:
        thread_id = thread_id or f"{user_id}_{uuid.uuid4().hex[:8]}"
        config = {"configurable": {"thread_id": thread_id}}

        saved_state = None
        try:
            saved_state = await workflow.aget_state(config)
            has_interrupt = bool(saved_state) and bool(saved_state.tasks or saved_state.next)
            logger.info(f"has_interrupt: {has_interrupt}")
        except Exception:
            has_interrupt = False
            logger.info("except has_interrupt: False")
        logger.info(f"Checking for interrupt - Thread: {thread_id}, saved_state: {saved_state}, Has Interrupt: {has_interrupt}")

        if has_interrupt:
            # 有中断，恢复执行
            logger.info(f"Resuming interrupted workflow for thread: {thread_id}")
            invoke_state = Command(resume=user_message)
        else:
            # 正常执行
            invoke_state = {
                "user_id": user_id,
                "thread_id": thread_id,
                "channel": channel,
                "messages": [HumanMessage(content=user_message)]
            }

        final_state = await workflow.ainvoke(invoke_state, config)

        # 检查是否因 interrupt 挂起（追问场景）
        interrupts = final_state.get("__interrupt__", [])
        if interrupts:
            question = interrupts[-1].value if hasattr(interrupts[-1], "value") else str(interrupts[-1])

            return {
                "reply": question,
                "thread_id": thread_id,
                "metadata": {"interrupted": True},
            }

        reply = final_state.get("final_reply") or "抱歉，我无法处理您的请求"
        logger.info(f"Agent processing completed - Thread: {thread_id}")

        return {
            "reply": reply,
            "thread_id": thread_id,
            "metadata": {
                "intent": final_state.get("intent"),
                "reason": final_state.get("reason", ""),
            },
        }

    except Exception as e:
        logger.error(f"Agent execution failed: {e}")
        return {
            "reply": f"抱歉，处理过程中出现错误: {str(e)}",
            "thread_id": thread_id or "unknown",
            "metadata": {"error": str(e)},
        }
