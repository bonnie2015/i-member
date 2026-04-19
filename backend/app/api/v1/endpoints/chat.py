from fastapi import APIRouter, Depends
from app.api.v1.models.chat import ChatRequest, ChatResponse
from app.security.jwt_auth import AuthContext, get_auth_context
from app.agents.tools.scrm_client import REQUEST_ACCESS_TOKEN_CTX, REQUEST_THREAD_ID_CTX, REQUEST_USER_ID_CTX
from app.workflow.graph import (
    invoke_member_ops,
)
import uuid

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    user_id = auth.claims.sub
    context_token = REQUEST_ACCESS_TOKEN_CTX.set(auth.access_token)
    user_context_token = REQUEST_USER_ID_CTX.set(user_id)
    thread_id = request.thread_id or f"{user_id}_{uuid.uuid4().hex[:8]}"
    thread_context_token = REQUEST_THREAD_ID_CTX.set(thread_id)
    try:
        result = await invoke_member_ops(
            user_message=request.message,
            user_id=user_id,
            thread_id=thread_id,
            channel=request.channel,
        )
        return ChatResponse(**result)
    finally:
        REQUEST_THREAD_ID_CTX.reset(thread_context_token)
        REQUEST_ACCESS_TOKEN_CTX.reset(context_token)
        REQUEST_USER_ID_CTX.reset(user_context_token)
