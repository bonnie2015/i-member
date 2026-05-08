from __future__ import annotations

import json
from typing import Any, Dict, List

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field

from app.agents.summary_agent import summary_agent
from app.config.config import settings
from app.config.logging import get_logger
from app.tools.business.execution_context import REQUEST_THREAD_ID_CTX, REQUEST_USER_ID_CTX

logger = get_logger("rag_tools")

from app.llm.runtime import estimate_tokens

_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
_SIMILARITY_TOP_K = 3
_MAX_CONTEXT_TOKENS = 3000

_index_instance = None


class RagSearchInput(BaseModel):
    query: str = Field(description="检索查询文本，用自然语言描述要查找的品牌政策或规则信息")


def _get_index():
    global _index_instance
    if _index_instance is not None:
        return _index_instance

    from llama_index.core import StorageContext, VectorStoreIndex
    from llama_index.embeddings.fastembed import FastEmbedEmbedding
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    embed_model = FastEmbedEmbedding(model_name=_EMBEDDING_MODEL)

    client = QdrantClient(url=settings.qdrant_url)
    vector_store = QdrantVectorStore(
        client=client,
        collection_name=settings.qdrant_collection,
        enable_hybrid=False,
    )

    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    _index_instance = VectorStoreIndex.from_vector_store(
        vector_store=vector_store,
        embed_model=embed_model,
    )

    logger.info("[rag_tools] index initialized collection=%s", settings.qdrant_collection)
    return _index_instance


async def _compress_if_needed(text: str, chunk_id: str) -> str:
    """超过 _MAX_CONTEXT_TOKENS 时调用摘要模型压缩。"""
    token_count = estimate_tokens(text)
    if token_count <= _MAX_CONTEXT_TOKENS:
        return text

    thread_id = str(REQUEST_THREAD_ID_CTX.get() or "").strip() or "unknown"
    user_id = str(REQUEST_USER_ID_CTX.get() or "").strip() or None

    logger.info(
        "[rag_search] context_guard triggered chunk_id=%s tokens=%s limit=%s",
        chunk_id,
        token_count,
        _MAX_CONTEXT_TOKENS,
    )

    compressed = await summary_agent.compress_rag(
        text,
        thread_id=thread_id,
        user_id=user_id,
    )
    if compressed:
        compressed_tokens = estimate_tokens(compressed)
        logger.info(
            "[rag_search] context_compressed chunk_id=%s tokens=%s→%s",
            chunk_id,
            token_count,
            compressed_tokens,
        )
        return compressed

    return text


@tool("rag_search", args_schema=RagSearchInput)
async def rag_search_tool(query: str) -> str:
    """品牌官方政策知识库检索工具。包含会员等级、售后退换货条款、隐私政策等。

    仅在现有信息无法确定用户具体要求，或缺少与品牌政策相关的数据，判断非常有必要对政策文档进行查找时，才调用此工具。

    Args:
        query: 检索查询文本，用自然语言描述要查找的品牌政策或规则信息

    Returns:
        检索到的相关政策原文片段
    """
    logger.info("[rag_search] query=%s", query)
    try:
        index = _get_index()
        retriever = index.as_retriever(similarity_top_k=_SIMILARITY_TOP_K)
        nodes = retriever.retrieve(query)

        if not nodes:
            logger.info("[rag_search] query=%s no_results", query[:50])
            return json.dumps(
                {"found": False, "message": "未检索到相关政策信息"},
                ensure_ascii=False,
            )

        fragments: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        trace_parts: list[str] = []

        for node_with_score in nodes:
            score = float(getattr(node_with_score, "score", 0) or 0)
            node = node_with_score.node
            chunk_id = str(getattr(node, "node_id", "") or "")
            text = str(getattr(node, "text", "") or "").strip()
            metadata = dict(getattr(node, "metadata", {}) or {})

            if not text:
                continue
            if chunk_id and chunk_id in seen_ids:
                continue
            if chunk_id:
                seen_ids.add(chunk_id)

            # Context Guard
            text = await _compress_if_needed(text, chunk_id)

            fragments.append(
                {
                    "score": round(score, 4),
                    "source": metadata.get("source_file", "unknown"),
                    "header_path": metadata.get("header_path", ""),
                    "content": text,
                }
            )

            # 链路追踪
            trace_parts.append(
                f"chunk={chunk_id[:20]} header={metadata.get('header_path', '')} tokens={estimate_tokens(text)}"
            )

        logger.info(
            "[rag_search] query=%s results=%s trace=%s",
            query[:80],
            len(fragments),
            " | ".join(trace_parts) if trace_parts else "(empty)",
        )
        return json.dumps(
            {"found": True, "fragments": fragments},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.error("[rag_search] failed: %s", exc)
        return json.dumps(
            {"found": False, "message": f"检索服务异常: {exc}"},
            ensure_ascii=False,
        )


_SIZE_GUIDE_TOP_K = 3
_SIZE_GUIDE_SOURCE_FILES = []  # 品牌方在此填入尺码对照表 PDF 文件名


@tool("size_guide_search", args_schema=RagSearchInput)
async def size_guide_search_tool(query: str) -> str:
    """尺码/版型知识检索工具。仅用于回答尺码选择、版型建议、穿着合身度等问题。

    从品牌尺码对照表、版型说明等文档中检索相关信息。

    Args:
        query: 检索查询文本，如"170身高穿多大码"、"这款偏大还是偏小"

    Returns:
        检索到的尺码/版型相关原文片段
    """
    logger.info("[size_guide_search] query=%s", query)
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchAny

        qdrant_filter = Filter(
            must=[
                FieldCondition(
                    key="source_file",
                    match=MatchAny(any=_SIZE_GUIDE_SOURCE_FILES),
                )
            ]
        )

        index = _get_index()
        retriever = index.as_retriever(
            similarity_top_k=_SIZE_GUIDE_TOP_K,
            vector_store_kwargs={"qdrant_filters": qdrant_filter},
        )
        nodes = retriever.retrieve(query)

        if not nodes:
            logger.info("[size_guide_search] query=%s no_results", query[:50])
            return json.dumps(
                {"found": False, "message": "未检索到相关尺码信息"},
                ensure_ascii=False,
            )

        fragments: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        trace_parts: list[str] = []

        for node_with_score in nodes:
            score = float(getattr(node_with_score, "score", 0) or 0)
            node = node_with_score.node
            chunk_id = str(getattr(node, "node_id", "") or "")
            text = str(getattr(node, "text", "") or "").strip()
            metadata = dict(getattr(node, "metadata", {}) or {})

            if not text:
                continue
            if chunk_id and chunk_id in seen_ids:
                continue
            if chunk_id:
                seen_ids.add(chunk_id)

            text = await _compress_if_needed(text, chunk_id)

            fragments.append(
                {
                    "score": round(score, 4),
                    "source": metadata.get("source_file", "unknown"),
                    "header_path": metadata.get("header_path", ""),
                    "content": text,
                }
            )
            trace_parts.append(
                f"chunk={chunk_id[:20]} header={metadata.get('header_path', '')} tokens={estimate_tokens(text)}"
            )

        logger.info(
            "[size_guide_search] query=%s results=%s trace=%s",
            query[:80],
            len(fragments),
            " | ".join(trace_parts) if trace_parts else "(empty)",
        )
        return json.dumps(
            {"found": True, "fragments": fragments},
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.error("[size_guide_search] failed: %s", exc)
        return json.dumps(
            {"found": False, "message": f"检索服务异常: {exc}"},
            ensure_ascii=False,
        )


async def warmup_rag() -> None:
    """启动时预热：加载 embedding 模型、连接 Qdrant。"""

    logger.info("[rag_tools] warmup start")
    _get_index()
    logger.info("[rag_tools] warmup done")


TOOLS: List[BaseTool] = [rag_search_tool, size_guide_search_tool]


def get_rag_tools() -> List[BaseTool]:
    return TOOLS


def get_size_guide_tools() -> List[BaseTool]:
    return [size_guide_search_tool]
