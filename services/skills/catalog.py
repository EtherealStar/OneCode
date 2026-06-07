"""Prompt-facing skill catalog provider."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Protocol

from core.runtime_state import RuntimeState
from services.skills.loader import get_commands
from services.skills.loader import find_command as loader_find_command
from services.skills.types import SkillCommand


class SkillCatalogProvider(Protocol):
    def visible_skills(
        self,
        state: RuntimeState,
        cwd: Path,
    ) -> Iterable[SkillCommand]:
        ...

    def find_skill(self, name: str, cwd: Path) -> SkillCommand | None:
        ...


class LoaderSkillCatalogProvider:
    def find_skill(self, name: str, cwd: Path) -> SkillCommand | None:
        """Find one loaded skill by name for the skill tool."""

        return loader_find_command(name, cwd)

    def visible_skills(
        self,
        state: RuntimeState,
        cwd: Path,
    ) -> tuple[SkillCommand, ...]:
        """Return skills the model may invoke from the current prompt catalog."""

        denied = _names(state.metadata.get("denied_skills"))
        disabled = _names(state.metadata.get("disabled_skills"))
        hidden = denied | disabled
        return tuple(
            command
            for command in get_commands(cwd)
            if command.user_invocable
            and not command.disable_model_invocation
            and command.name not in hidden
        )


def _names(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value.lstrip("/")} if value else set()
    try:
        return {str(item).lstrip("/") for item in value if str(item)}
    except TypeError:
        return {str(value).lstrip("/")} if str(value) else set()
