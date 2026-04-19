"""
Skills registry - skill 目录扫描、快照生成与加载。
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.logging import get_logger

logger = get_logger("skills_registry")

_SKILLS_DIR = Path(__file__).parent
_SNAPSHOT_PATH = _SKILLS_DIR / "skills-snapshot.md"
_LIST_FIELDS = {"available_tools", "clarify_labels"}
_REGISTRY_ROOT = _SKILLS_DIR.parent.parent.parent


def list_skills() -> List[Dict[str, str]]:
    skills: List[Dict[str, Any]] = []
    for skill_md in sorted(_SKILLS_DIR.glob("*/SKILL.md")):
        text = skill_md.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(text)
        name = str(frontmatter.get("name") or "").strip()
        description = str(frontmatter.get("description") or "").strip()
        if not name or not description:
            continue
        skills.append(
            {
                "name": name,
                "description": description,
                "available_tools": list(frontmatter.get("available_tools") or []),
                "clarify_labels": list(frontmatter.get("clarify_labels") or []),
                "location": str(skill_md.relative_to(_REGISTRY_ROOT)),
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
                f"        <available_tools>{', '.join(skill['available_tools'])}</available_tools>",
                f"        <clarify_labels>{', '.join(skill['clarify_labels'])}</clarify_labels>",
                f"        <location>{skill['location']}</location>",
                "    </skill>",
            ]
        )
    lines.append("</skills>")
    _SNAPSHOT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(f"Skills snapshot built: {len(skills)} skills -> {_SNAPSHOT_PATH}")


def _parse_frontmatter(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        return {}

    fields: Dict[str, Any] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if not key:
            continue
        if key in _LIST_FIELDS:
            fields[key] = _split_list_value(value)
        else:
            fields[key] = value
    return fields


def _split_list_value(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def load_skills_snapshot() -> str:
    build_skills_snapshot()
    return _SNAPSHOT_PATH.read_text(encoding="utf-8").strip()


def _resolve_skill_location(location: str) -> Path:
    normalized = str(location or "").strip()
    if not normalized:
        raise ValueError("skill location is empty")

    path = (_REGISTRY_ROOT / normalized).resolve()
    if not str(path).startswith(str(_REGISTRY_ROOT.resolve())):
        raise ValueError(f"skill location escapes registry root: {location}")
    return path


@lru_cache(maxsize=32)
def load_skill_context(location: Optional[str] = None) -> str:
    if not location:
        return load_skills_snapshot()

    skill_path = _resolve_skill_location(location)
    if not skill_path.exists():
        return f"[skill at '{location}' 不存在]"
    return skill_path.read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def load_skill(name: str) -> str:
    skill_path = _SKILLS_DIR / name / "SKILL.md"
    if not skill_path.exists():
        return f"[skill '{name}' 不存在]"
    return skill_path.read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def load_skill_metadata(name: str) -> Dict[str, Any]:
    for skill in list_skills():
        if skill.get("name") == name:
            return dict(skill)
    return {}


@lru_cache(maxsize=32)
def load_skill_metadata_by_location(location: str) -> Dict[str, Any]:
    normalized = str(location or "").strip()
    if not normalized:
        return {}
    for skill in list_skills():
        if str(skill.get("location") or "").strip() == normalized:
            return dict(skill)
    return {}
