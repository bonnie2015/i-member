"""
Skills registry - skill 目录扫描、快照生成与加载。
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config.logging import get_logger

logger = get_logger("skills_registry")

_SKILLS_DIR = Path(__file__).parent
_LIST_FIELDS = {"available_tools", "clarify_labels"}
_REGISTRY_ROOT = _SKILLS_DIR.parent.parent.parent
_DEFAULT_SKILL_GROUP = "ticket"


def _get_skill_group_dirs() -> List[Path]:
    return sorted(
        path
        for path in _SKILLS_DIR.iterdir()
        if path.is_dir()
        and not path.name.startswith("__")
        and any(path.glob("*/SKILL.md"))
    )


def _get_snapshot_path(group: str) -> Path:
    normalized = str(group or "").strip()
    if not normalized:
        raise ValueError("skill group is empty")
    return _SKILLS_DIR / f"{normalized}-skills-snapshot.md"


def list_skills(group: Optional[str] = None) -> List[Dict[str, str]]:
    target_group = str(group or _DEFAULT_SKILL_GROUP).strip()
    group_dir = _SKILLS_DIR / target_group
    if not group_dir.exists() or not group_dir.is_dir():
        return []

    skills: List[Dict[str, Any]] = []
    for skill_md in sorted(group_dir.glob("*/SKILL.md")):
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
                "group": target_group,
                "location": str(skill_md.relative_to(_REGISTRY_ROOT)),
            }
        )
    return skills


def build_skills_snapshot(group: Optional[str] = None) -> None:
    target_group = str(group or _DEFAULT_SKILL_GROUP).strip()
    snapshot_path = _get_snapshot_path(target_group)
    lines: List[str] = ["<skills>"]
    skills = list_skills(target_group)
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
    snapshot_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info(
        f"Skills snapshot built: group={target_group}, count={len(skills)} -> {snapshot_path}"
    )


def build_all_skills_snapshots() -> None:
    for group_dir in _get_skill_group_dirs():
        build_skills_snapshot(group_dir.name)


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


def load_skills_snapshot(group: Optional[str] = None) -> str:
    target_group = str(group or _DEFAULT_SKILL_GROUP).strip()
    snapshot_path = _get_snapshot_path(target_group)
    build_skills_snapshot(target_group)
    return snapshot_path.read_text(encoding="utf-8").strip()


def _resolve_skill_location(location: str) -> Path:
    normalized = str(location or "").strip()
    if not normalized:
        raise ValueError("skill location is empty")

    path = (_REGISTRY_ROOT / normalized).resolve()
    if not str(path).startswith(str(_REGISTRY_ROOT.resolve())):
        raise ValueError(f"skill location escapes registry root: {location}")
    return path


@lru_cache(maxsize=32)
def load_skill_context(
    location: Optional[str] = None, group: Optional[str] = None
) -> str:
    if not location:
        return load_skills_snapshot(group=group)

    skill_path = _resolve_skill_location(location)
    if not skill_path.exists():
        return f"[skill at '{location}' 不存在]"
    return skill_path.read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def load_skill(name: str, group: Optional[str] = None) -> str:
    target_group = str(group or _DEFAULT_SKILL_GROUP).strip()
    skill_path = _SKILLS_DIR / target_group / name / "SKILL.md"
    if not skill_path.exists():
        return f"[skill '{name}' 不存在]"
    return skill_path.read_text(encoding="utf-8")


@lru_cache(maxsize=16)
def load_skill_metadata(name: str, group: Optional[str] = None) -> Dict[str, Any]:
    for skill in list_skills(group):
        if skill.get("name") == name:
            return dict(skill)
    return {}


@lru_cache(maxsize=32)
def load_skill_metadata_by_location(location: str) -> Dict[str, Any]:
    normalized = str(location or "").strip()
    if not normalized:
        return {}
    for group_dir in _get_skill_group_dirs():
        for skill in list_skills(group_dir.name):
            if str(skill.get("location") or "").strip() == normalized:
                return dict(skill)
    return {}
