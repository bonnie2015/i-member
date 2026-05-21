from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import chat
from app.config.logging import get_logger
from app.config.redis import close_redis_client
from app.skills.registry import build_all_skills_snapshots
import app.workflow.graph as graph_module
from app.memory.redis_checkpointer import create_checkpointer

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：生成 skills 快照
    build_all_skills_snapshots()
    # 启动：初始化 checkpointer（含 Redis 索引创建）并编译工作流
    checkpointer = await create_checkpointer()
    graph_module.workflow = graph_module.create_workflow(checkpointer)
    logger.info("Workflow initialized")
    # 启动：预热 RAG（加载 embedding 模型、tokenizer、连接 Qdrant）
    from app.tools.rag_tools import warmup_rag

    await warmup_rag()
    yield
    await close_redis_client()


app = FastAPI(
    title="Member-Ops Agent",
    description="全渠道会员智能运营系统",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/v1", tags=["chat"])


@app.get("/health")
async def health():
    return {"status": "ok"}
