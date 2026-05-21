from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from typing import Any, Dict

from qdrant_client import QdrantClient

from app.config.config import settings
from app.config.logging import get_logger

logger = get_logger("rag_ingest")

_DATA_DIR = Path("/app/data/brand_docs")
_CACHE_DIR = Path("/app/data/rag_docs")
_EMBEDDING_MODEL = "BAAI/bge-small-zh-v1.5"
_COLLECTION_NAME = settings.qdrant_collection
_CHUNK_SIZE = 512
_CHUNK_OVERLAP = 50


def _build_embed_model():
    from llama_index.embeddings.fastembed import FastEmbedEmbedding

    return FastEmbedEmbedding(model_name=_EMBEDDING_MODEL)


def _build_vector_store():
    from llama_index.vector_stores.qdrant import QdrantVectorStore
    from qdrant_client import QdrantClient

    client = QdrantClient(url=settings.qdrant_url)
    return QdrantVectorStore(
        client=client,
        collection_name=_COLLECTION_NAME,
        enable_hybrid=True,
        fastembed_sparse_model="Qdrant/bm25",
    )


async def _parse_pdf(pdf_path: Path) -> str:
    from llama_parse import LlamaParse

    if not settings.llama_cloud_api_key:
        raise RuntimeError(
            "LLAMA_CLOUD_API_KEY is not set. "
            "Get one at https://cloud.llamaindex.ai and add to .env"
        )

    kwargs: Dict[str, Any] = dict(
        api_key=settings.llama_cloud_api_key,
        result_type="markdown",
        verbose=True,
    )
    if "scanned_" in pdf_path.name.lower():
        kwargs["use_vendor_multimodal_model"] = True
        logger.info("[ingest] multimodal enabled for %s", pdf_path.name)

    parser = LlamaParse(**kwargs)
    documents = await parser.aload_data(str(pdf_path))
    return "\n\n".join(doc.text for doc in documents)


def _md5_hex(file_path: Path) -> str:
    return hashlib.md5(file_path.read_bytes()).hexdigest()


def _cache_md_path(pdf_path: Path, md5: str) -> Path:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{pdf_path.stem}_md5_{md5}.md"


def _parse_nodes(markdown_text: str):
    """按 H1/H2 标题切块，细标题降级为粗体，超长再按 chunk_size 细分。"""
    import re
    from llama_index.core import Document as LlamaDocument
    from llama_index.core.node_parser import MarkdownNodeParser, SentenceSplitter

    # 将 ### 及更深标题降级为粗体，避免被切得太碎
    collapsed = re.sub(r"^(#{3,})\s", r"**", markdown_text, flags=re.MULTILINE)

    doc = LlamaDocument(text=collapsed)
    header_nodes = MarkdownNodeParser().get_nodes_from_documents([doc])

    splitter = SentenceSplitter(
        chunk_size=_CHUNK_SIZE,
        chunk_overlap=_CHUNK_OVERLAP,
    )
    return splitter.get_nodes_from_documents(header_nodes)


async def ingest_directory(directory: Path | None = None) -> Dict[str, Any]:
    data_dir = directory or _DATA_DIR
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    pdf_files = sorted(data_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"No PDF files found in {data_dir}")

    logger.info("[ingest] start files=%s", [f.name for f in pdf_files])

    # 清理旧数据（先删后重建，确保 vector_store 连到新 collection）
    qdrant_client = QdrantClient(url=settings.qdrant_url)
    if qdrant_client.collection_exists(_COLLECTION_NAME):
        qdrant_client.delete_collection(_COLLECTION_NAME)
        logger.info("[ingest] deleted old collection %s", _COLLECTION_NAME)

    embed_model = _build_embed_model()
    vector_store = _build_vector_store()

    from llama_index.core import StorageContext, VectorStoreIndex

    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
    )

    all_nodes: list[Any] = []
    for pdf_path in pdf_files:
        md5 = _md5_hex(pdf_path)
        cache_path = _cache_md_path(pdf_path, md5)

        if cache_path.exists():
            logger.info(
                "[ingest] md5_cache_hit %s -> %s", pdf_path.name, cache_path.name
            )
            markdown_text = cache_path.read_text(encoding="utf-8")
        else:
            logger.info("[ingest] parsing %s md5=%s", pdf_path.name, md5)
            try:
                markdown_text = await _parse_pdf(pdf_path)
            except Exception as exc:
                logger.error("[ingest] parse failed %s: %s", pdf_path.name, exc)
                continue
            cache_path.write_text(markdown_text, encoding="utf-8")
            logger.info("[ingest] cached %s", cache_path.name)

        logger.info("[ingest] parsed %s length=%s", pdf_path.name, len(markdown_text))

        nodes = _parse_nodes(markdown_text)
        for node in nodes:
            node.metadata["source_file"] = pdf_path.name
        all_nodes.extend(nodes)
        logger.info("[ingest] nodes from %s count=%s", pdf_path.name, len(nodes))

    if not all_nodes:
        raise RuntimeError("No nodes generated from any PDF")

    logger.info("[ingest] total_chunks=%s building_index", len(all_nodes))

    VectorStoreIndex(
        nodes=all_nodes,
        storage_context=storage_context,
        embed_model=embed_model,
    )

    logger.info(
        "[ingest] done collection=%s chunks=%s", _COLLECTION_NAME, len(all_nodes)
    )
    return {
        "collection": _COLLECTION_NAME,
        "chunks": len(all_nodes),
        "files": [f.name for f in pdf_files],
    }


def main():
    result = asyncio.run(ingest_directory())
    logger.info("[ingest] result=%s", result)


if __name__ == "__main__":
    main()
