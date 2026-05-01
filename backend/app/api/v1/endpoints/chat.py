from fastapi import APIRouter, Depends, HTTPException, status
from app.api.v1.chat_history import (
    append_chat_message,
    load_chat_messages,
    load_last_chat_thread,
)
from app.api.v1.models.chat import ChatMessageRecord, ChatRequest, ChatResponse, LatestThreadResponse
from app.security.jwt_auth import AuthContext, get_auth_context
from app.agents.tools.business.execution_context import REQUEST_ACCESS_TOKEN_CTX, REQUEST_THREAD_ID_CTX, REQUEST_USER_ID_CTX
from app.workflow.graph import (
    get_thread_owner_user_id,
    invoke_member_ops,
)
import uuid

router = APIRouter()


def _parse_history_message(raw_message: dict) -> ChatMessageRecord | None:
    role = str(raw_message.get("role") or "").strip()
    if role not in {"user", "assistant"}:
        return None

    content = str(raw_message.get("content") or "").strip()
    products = [item for item in list(raw_message.get("products") or []) if isinstance(item, dict)]
    interaction = raw_message.get("interaction")
    if not content and not products and interaction is None:
        return None
    return ChatMessageRecord(role=role, content=content, products=products, interaction=interaction)


@router.get("/chat/latest-thread", response_model=LatestThreadResponse)
async def latest_thread(
    auth: AuthContext = Depends(get_auth_context),
):
    user_id = auth.claims.sub
    thread_id = await load_last_chat_thread(user_id)
    if not thread_id:
        return LatestThreadResponse(thread_id=None, messages=[])

    raw_messages = await load_chat_messages(user_id, thread_id)
    messages = [
        parsed
        for parsed in (_parse_history_message(item) for item in raw_messages)
        if parsed is not None
    ]
    return LatestThreadResponse(thread_id=thread_id, messages=messages)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    auth: AuthContext = Depends(get_auth_context),
):
    user_id = auth.claims.sub
    if request.thread_id:
        owner_user_id = await get_thread_owner_user_id(request.thread_id)
        if owner_user_id and owner_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="thread_id does not belong to current user",
            )

    context_token = REQUEST_ACCESS_TOKEN_CTX.set(auth.access_token)
    user_context_token = REQUEST_USER_ID_CTX.set(user_id)
    thread_id = request.thread_id or f"{user_id}_{uuid.uuid4().hex[:8]}"
    thread_context_token = REQUEST_THREAD_ID_CTX.set(thread_id)
    try:
        await append_chat_message(
            user_id,
            thread_id,
            {
                "role": "user",
                "content": request.message,
            },
        )
        result = await invoke_member_ops(
            user_message=request.message,
            user_id=user_id,
            thread_id=thread_id,
            channel=request.channel,
        )
        response = ChatResponse(**result)
        await append_chat_message(
            user_id,
            thread_id,
            {
                "role": "assistant",
                "content": response.reply,
                "interaction": response.interaction.model_dump(mode="json") if response.interaction else None,
                "products": [item.model_dump(mode="json") for item in response.products],
            },
        )
        return response
    finally:
        REQUEST_THREAD_ID_CTX.reset(thread_context_token)
        REQUEST_ACCESS_TOKEN_CTX.reset(context_token)
        REQUEST_USER_ID_CTX.reset(user_context_token)
