from __future__ import annotations

from pathlib import Path
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field, model_validator

DEFAULT_MAX_BYTES = 50 * 1024
MAX_PAGES = 8
PAGE_LINES = 200


def _detect_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if parent.name == "app" and (parent / "workflow").is_dir() and (parent / "agents").is_dir():
            return parent.parent
        if (parent / "backend").is_dir() and (parent / "backend" / "app").is_dir():
            return parent
    return current.parents[3]


ROOT = _detect_root()


class ReadInput(BaseModel):
    path: Optional[str] = None
    file_path: Optional[str] = Field(default=None, alias="file_path")
    offset: int = 1
    limit: Optional[int] = None

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, value):
        if isinstance(value, dict):
            if not value.get("path") and value.get("file_path"):
                value["path"] = value["file_path"]
        return value


def resolve_under_root(root: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    abs_path = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if abs_path != root and root not in abs_path.parents:
        raise ValueError(f"path escapes workspace root: {raw_path}")
    return abs_path


def read_with_adaptive_paging(file_path: Path, offset: int, max_bytes: int) -> str:
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    index = max(offset - 1, 0)
    out = []
    used = 0
    pages = 0

    while index < len(lines) and pages < MAX_PAGES:
        pages += 1
        page_lines = lines[index : index + PAGE_LINES]
        page_text = "\n".join(page_lines)
        chunk = ("\n\n" if out else "") + page_text
        chunk_bytes = len(chunk.encode("utf-8"))

        if out and used + chunk_bytes > max_bytes:
            break

        out.append(page_text)
        used += chunk_bytes
        index += len(page_lines)

        if used >= max_bytes:
            break

    text = "\n\n".join(out)
    if index < len(lines):
        text += f"\n\n[Read output capped at {max_bytes} bytes. Use offset={index + 1} to continue.]"
    return text


@tool("read_file", args_schema=ReadInput)
def read_file(
    path: Optional[str] = None,
    file_path: Optional[str] = None,
    offset: int = 1,
    limit: Optional[int] = None,
) -> str:
    """
    Read file contents safely within the current workspace root.
    Accepts path or file_path.
    """
    final_path = path or file_path
    if not final_path or not final_path.strip():
        raise ValueError("Missing required parameter: path (or file_path)")

    abs_path = resolve_under_root(ROOT, final_path)

    if not abs_path.exists():
        raise FileNotFoundError(str(abs_path))
    if abs_path.is_dir():
        raise IsADirectoryError(str(abs_path))

    if limit is not None and limit > 0:
        lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(offset - 1, 0)
        return "\n".join(lines[start : start + limit])

    return read_with_adaptive_paging(abs_path, offset=offset, max_bytes=DEFAULT_MAX_BYTES)
