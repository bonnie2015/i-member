from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from app.models.schemas import ChatRequest, ChatResponse
from app.config.logging import get_logger
from app.workflow.graph import run_member_ops_agent, workflow
from langchain_core.messages import HumanMessage
import json
import asyncio

logger = get_logger("chat_endpoint")

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        result = await run_member_ops_agent(
            user_message=request.message,
            user_id=request.user_id,
            thread_id=request.thread_id,
            channel=request.channel
        )

        return ChatResponse(
            reply=result["reply"],
            thread_id=result["thread_id"],
            metadata=result["metadata"]
        )

    except Exception as e:
        logger.error(f"Chat processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"处理失败: {str(e)}")


@router.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    try:
        async def generate_stream():
            thread_id = request.thread_id or f"{request.user_id}_{asyncio.get_event_loop().time()}"

            initial_state = {
                "user_id": request.user_id,
                "thread_id": thread_id,
                "channel": request.channel,
                "messages": [HumanMessage(content=request.message)]
            }

            config = {"configurable": {"thread_id": thread_id}}

            # 发送线程ID
            yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"

            # 流式执行工作流
            async for chunk in workflow.astream(initial_state, config):
                # 提取关键信息
                if "intent" in chunk:
                    yield f"data: {json.dumps({'type': 'intent', 'data': chunk['intent']})}\n\n"

                if "reason" in chunk:
                    yield f"data: {json.dumps({'type': 'reasoning', 'data': chunk['reason']})}\n\n"

                if "final_reply" in chunk:
                    # 模拟逐字输出
                    reply = chunk["final_reply"]
                    for char in reply:
                        yield f"data: {json.dumps({'type': 'content', 'data': char})}\n\n"
                        await asyncio.sleep(0.01)  # 模拟打字延迟

                # 发送情绪分数（如果有）
                if "emotion_score" in chunk:
                    yield f"data: {json.dumps({'type': 'emotion', 'data': chunk['emotion_score']})}\n\n"

            # 发送结束标记
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )

    except Exception as e:
        logger.error(f"Chat stream processing failed: {e}")
        raise HTTPException(status_code=500, detail=f"流式处理失败: {str(e)}")
