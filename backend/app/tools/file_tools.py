"""通用文件读取工具。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool


@tool
def read_file(path: str) -> str:
    """读取项目内文件内容。可用于查询政策、规则等知识文档。

    Args:
        path: 相对路径，如 "app/skills/ticket/refund-ticket/policy.md"

    Returns:
        文件内容；若文件不存在则返回提示。
    """
    full_path = Path(__file__).resolve().parent.parent.parent / path
    if not full_path.exists():
        return f"文件不存在: {path}"
    if not full_path.is_file():
        return f"不是文件: {path}"
    return full_path.read_text(encoding="utf-8")
