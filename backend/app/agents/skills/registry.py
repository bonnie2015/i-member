"""
Skills registry - skill 目录扫描、快照生成与加载。
"""

import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from app.config.logging import get_logger

logger = get_logger("skills_registry")

_SKILLS_DIR = Path(__file__).parent
_SNAPSHOT_PATH = _SKILLS_DIR / "skills-snapshot.md"


def list_skills() -> List[Dict[str, str]]:
    skills: List[Dict[str, str]] = []
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        name = _extract_frontmatter_field(text, "name")
        description = _extract_frontmatter_field(text, "description")
        if not name or not description:
            continue
        skills.append(
            {
                "name": name,
                "description": description,
                "location": str(skill_md.relative_to(_SKILLS_DIR.parent.parent.parent)),
            }
        )
    return skills


def build_skills_snapshot() -> None:
    lines: List[str] = ["<skills>"]
    skills = list_skills()
    for skill in skills:
        lines.extend(
            [
                "    <skill>",
                f"        <name>{skill['name']}</name>",
                f"        <description>{skill['description']}</description>",
                f"        <location>{skill['location']}</location>",
                "    </skill>",
            ]
        )
    lines.append("</skills>")
    _SNAPSHOT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Skills snapshot built: {len(skills)} skills -> {_SNAPSHOT_PATH}")


def _extract_frontmatter_field(text: str, field: str) -> Optional[str]:
    pattern = rf"^{field}:\s*(.+)$"
    matched = re.search(pattern, text, re.MULTILINE)
    return matched.group(1).strip() if matched else None


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
