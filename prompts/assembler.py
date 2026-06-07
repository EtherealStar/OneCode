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
    from services.memory import InstructionMemoryLoader, LongTermMemoryPromptProvider
    from services.skills import SkillCatalogProvider
    from services.tools.registry import ToolRegistry


class DynamicPromptAssembler:
    """Assemble system prompt text from current runtime state."""

    def __init__(
        self,
        cwd: Path | str | Callable[[], Path | str],
        tool_registry: "ToolRegistry | None" = None,
        skill_provider: "SkillCatalogProvider | None" = None,
        instruction_memory_loader: "InstructionMemoryLoader | None" = None,
        long_term_memory_provider: "LongTermMemoryPromptProvider | None" = None,
        section_cache: PromptSectionCache | None = None,
    ) -> None:
        self._cwd = cwd
        self._tool_registry = tool_registry
        self._skill_provider = skill_provider
        self._instruction_memory_loader = instruction_memory_loader
        self._long_term_memory_provider = long_term_memory_provider
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
        instruction_memory = ""
        instruction_memory_fingerprint = ""
        if self._instruction_memory_loader is not None:
            result = self._instruction_memory_loader.load(
                state,
                cwd,
                target_paths=tuple(Path(path) for path in _files_read_from_state(state)),
            )
            instruction_memory = result.rendered_text
            instruction_memory_fingerprint = result.fingerprint
        long_term_memory_prompt = ""
        long_term_memory_fingerprint = ""
        if self._long_term_memory_provider is not None:
            long_term_memory_prompt = self._long_term_memory_provider.prompt_text()
            long_term_memory_fingerprint = self._long_term_memory_provider.fingerprint()
        return PromptRuntimeContext(
            state=state,
            cwd=cwd,
            visible_tools=visible_tools,
            visible_skills=visible_skills,
            files_read=_files_read_from_state(state),
            transition=(
                state.last_transition.value if state.last_transition is not None else None
            ),
            mcp_server_instructions=_mcp_instructions_from_state(state),
            instruction_memory=instruction_memory,
            instruction_memory_fingerprint=instruction_memory_fingerprint,
            long_term_memory_prompt=long_term_memory_prompt,
            long_term_memory_fingerprint=long_term_memory_fingerprint,
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


def _mcp_instructions_from_state(state: RuntimeState) -> dict[str, str]:
    raw = state.metadata.get("mcp_server_instructions")
    if not isinstance(raw, dict):
        return {}
    instructions: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str):
            continue
        text = value.strip()
        if text:
            instructions[name] = text
    return instructions
