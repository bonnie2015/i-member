"""
Skills Utilities - 独立工具函数，用于处理 skill 文件

提供扫描、快照生成、加载等功能。
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from app.config.logging import get_logger

logger = get_logger("skills_utils")

_SKILLS_DIR = Path(__file__).parent.parent / "skills"
_SNAPSHOT_PATH = _SKILLS_DIR / "SKILLS_SNAPSHOT.md"


def build_skills_snapshot() -> None:
    """启动时调用：扫描所有 skill，生成 SKILLS_SNAPSHOT.md。"""
    lines: List[str] = []
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        name = _extract_frontmatter_field(text, "name")
        description = _extract_frontmatter_field(text, "description")
        if name and description:
            lines.append(f"[{name}]: {description}")
    _SNAPSHOT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Skills snapshot built: {len(lines)} skills → {_SNAPSHOT_PATH}")


def _extract_frontmatter_field(text: str, field: str) -> Optional[str]:
    pattern = rf"^{field}:\s*(.+)$"
    m = re.search(pattern, text, re.MULTILINE)
    return m.group(1).strip() if m else None


def load_skills_snapshot() -> str:
    if not _SNAPSHOT_PATH.exists():
        build_skills_snapshot()
    return _SNAPSHOT_PATH.read_text(encoding="utf-8").strip()


@lru_cache(maxsize=16)
def load_skill(name: str) -> str:
    skill_path = _SKILLS_DIR / name / "SKILL.md"
    if not skill_path.exists():
        return f"[skill '{name}' 不存在]"
    return skill_path.read_text(encoding="utf-8")
