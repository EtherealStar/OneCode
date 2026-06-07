"""Dynamic system prompt assembler."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.runtime_state import RuntimeState
from prompts.cache import PromptSectionCache
from prompts.runtime_context import PromptRuntimeContext
from prompts.sections import PromptSection, default_sections

if TYPE_CHECKING:
    from services.skills import SkillCatalogProvider
    from services.tools.registry import ToolRegistry


class DynamicPromptAssembler:
    """Assemble system prompt text from current runtime state."""

    def __init__(
        self,
        cwd: Path | str | Callable[[], Path | str],
        tool_registry: "ToolRegistry | None" = None,
        skill_provider: "SkillCatalogProvider | None" = None,
        section_cache: PromptSectionCache | None = None,
    ) -> None:
        self._cwd = cwd
        self._tool_registry = tool_registry
        self._skill_provider = skill_provider
        self._section_cache = section_cache or PromptSectionCache()

    @property
    def section_cache(self) -> PromptSectionCache:
        return self._section_cache

    def assemble(self, state: RuntimeState) -> str:
        context = self._build_context(state)
        rendered = [
            self._render_section(section)
            for section in default_sections(context)
            if section.body.strip()
        ]
        return "\n\n".join(rendered)

    def _build_context(self, state: RuntimeState) -> PromptRuntimeContext:
        cwd = self._resolve_cwd()
        visible_tools = ()
        if self._tool_registry is not None:
            visible_tools = self._tool_registry.visible_descriptors(state)
        visible_skills = ()
        if self._skill_provider is not None:
            visible_skills = tuple(self._skill_provider.visible_skills(state, cwd))
        return PromptRuntimeContext(
            state=state,
            cwd=cwd,
            visible_tools=visible_tools,
            visible_skills=visible_skills,
            files_read=_files_read_from_state(state),
            transition=(
                state.last_transition.value if state.last_transition is not None else None
            ),
        )

    def _resolve_cwd(self) -> Path:
        cwd = self._cwd() if callable(self._cwd) else self._cwd
        return Path(cwd).resolve()

    def _render_section(self, section: PromptSection) -> str:
        if not section.cacheable:
            return section.render()
        cached = self._section_cache.get(section.key, section.fingerprint)
        if cached is not None:
            return cached
        rendered = section.render()
        self._section_cache.set(section.key, section.fingerprint, rendered)
        return rendered


def _files_read_from_state(state: RuntimeState) -> tuple[str, ...]:
    raw = state.metadata.get("files_read", ())
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)):
        values: tuple[Any, ...] = (raw,)
    else:
        try:
            values = tuple(raw)
        except TypeError:
            values = (raw,)
    return tuple(sorted({str(value) for value in values if str(value).strip()}))
