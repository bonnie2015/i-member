from __future__ import annotations

from typing import Any, Dict

from app.tools import ask_user_tool, finish_step_tool, get_scrm_tools, onitsuka_get_product_detail
from app.tools.memory_tools import get_memory_tools


def _tool_registry() -> Dict[str, Any]:
    return {
        str(tool.name): tool
        for tool in [*get_scrm_tools(), onitsuka_get_product_detail, *get_memory_tools()]
    }


def _resolve_step_tools(step: Dict[str, Any]) -> list:
    """解析 step 的 available_tools，始终追加 ask_user 和 finish_step。"""
    registry = _tool_registry()
    selected = []
    raw_tool_names = step.get("available_tools") or []
    if isinstance(raw_tool_names, str):
        raw_tool_names = [raw_tool_names]
    for item in (raw_tool_names or []):
        tool_name = str(item or "").strip()
        tool = registry.get(tool_name)
        if tool:
            selected.append(tool)
    return [*selected, ask_user_tool, finish_step_tool]
