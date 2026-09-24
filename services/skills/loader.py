"""基于文件系统且具备确定性优先级的技能发现。"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

from services.skills.frontmatter import parse_skill_markdown
from services.skills.types import SkillCommand

_BUNDLED_SKILLS: tuple[SkillCommand, ...] = ()
_CACHE: dict[str, tuple[SkillCommand, ...]] = {}


def init_bundled_skills(commands: Iterable[SkillCommand] = ()) -> None:
    """注册进程级内置技能并清空缓存的合并目录。"""

    global _BUNDLED_SKILLS
    _BUNDLED_SKILLS = tuple(sorted(commands, key=lambda command: command.name))
    clear_skill_caches()


def get_commands(cwd: Path | str) -> tuple[SkillCommand, ...]:
    """返回工作区目录缓存的合并命令。"""

    key = str(Path(cwd).resolve())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    commands = load_all_commands(cwd)
    _CACHE[key] = commands
    return commands


def load_all_commands(cwd: Path | str) -> tuple[SkillCommand, ...]:
    """按内置、用户、项目的顺序加载技能，后加载的来源覆盖前者。"""

    workspace = Path(cwd).resolve()
    merged: dict[str, SkillCommand] = {}
    for command in _BUNDLED_SKILLS:
        merged[command.name] = command
    for command in _load_from_dir(_user_skills_dir(), "user"):
        merged[command.name] = command
    for command in _load_from_dir(workspace / ".onecode" / "skills", "project"):
        merged[command.name] = command
    return tuple(merged[name] for name in sorted(merged))


def find_command(name: str, cwd: Path | str) -> SkillCommand | None:
    """按规范化名称查找单个命令，允许前导斜杠。"""

    normalized = name.strip().lstrip("/")
    if not normalized:
        return None
    for command in get_commands(cwd):
        if command.name == normalized:
            return command
    return None


def clear_skill_caches() -> None:
    """清空已缓存的文件系统技能目录。"""

    _CACHE.clear()


def _user_skills_dir() -> Path:
    base = os.environ.get("ONECODE_HOME")
    if base and base.strip():
        return Path(base).expanduser() / "skills"
    return Path.home() / ".onecode" / "skills"


def _load_from_dir(skills_dir: Path, source: str) -> tuple[SkillCommand, ...]:
    if not skills_dir.exists() or not skills_dir.is_dir():
        return ()
    commands: list[SkillCommand] = []
    for child in sorted(skills_dir.iterdir(), key=lambda item: item.name):
        if not child.is_dir():
            continue
        skill_file = child / "SKILL.md"
        if not skill_file.is_file():
            continue
        command = _load_file(skill_file, source=source, fallback_name=child.name)
        if command is not None:
            commands.append(command)
    return tuple(commands)


def _load_file(
    skill_file: Path,
    *,
    source: str,
    fallback_name: str,
) -> SkillCommand | None:
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    command = parse_skill_markdown(
        text,
        source=source,  # type: ignore[arg-type]
        root=skill_file.parent,
        fallback_name=fallback_name,
    )
    if command is None or not command.content.strip():
        return None
    return command
