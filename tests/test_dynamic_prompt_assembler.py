from __future__ import annotations

from pathlib import Path
from typing import Any

from core.context_engine import ContextEngine
from core.runtime_state import RuntimeState
from prompts.assembler import DynamicPromptAssembler
from prompts.cache import PromptSectionCache
from services.context.message_store import MessageStore
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
)


def make_descriptor(name: str, *, prompt: str = "") -> ToolDescriptor:
    def handler(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool_call_id="",
            tool_name=name,
            content="ok",
        )

    def classify_input(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
        )

    return ToolDescriptor(
        name=name,
        description=f"{name} description",
        input_schema={
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        handler=handler,
        prompt=prompt,
        classify_input=classify_input,
    )


def test_dynamic_prompt_includes_stable_sections_and_workspace_state(
    tmp_path: Path,
) -> None:
    state = RuntimeState(session_id="session-hidden")
    state.metadata["files_read"] = {
        str(tmp_path / "b.py"),
        str(tmp_path / "a.py"),
    }
    registry = ToolRegistry(
        [
            make_descriptor("z_tool", prompt="Use z carefully."),
            make_descriptor("a_tool", prompt="Use a first."),
            make_descriptor("empty_tool"),
        ]
    )
    assembler = DynamicPromptAssembler(tmp_path, tool_registry=registry)

    prompt = assembler.assemble(state)

    assert prompt.startswith("# Identity\n")
    assert "# Behavior Rules\n" in prompt
    assert "# Workspace State\n" in prompt
    assert f"cwd: {tmp_path.resolve()}" in prompt
    assert "available tools: a_tool, empty_tool, z_tool" in prompt
    assert f"- {tmp_path / 'a.py'}" in prompt
    assert f"- {tmp_path / 'b.py'}" in prompt
    assert prompt.index("# Available Tools") < prompt.index("# Tool: a_tool")
    assert prompt.index("# Tool: a_tool") < prompt.index("# Tool: z_tool")
    assert "# Tool: empty_tool" not in prompt
    assert "Use a first." in prompt
    assert "Use z carefully." in prompt
    assert "session-hidden" not in prompt


def test_section_cache_reuses_unchanged_sections_and_invalidates_workspace(
    tmp_path: Path,
) -> None:
    state = RuntimeState()
    cache = PromptSectionCache()
    assembler = DynamicPromptAssembler(tmp_path, section_cache=cache)

    first_prompt = assembler.assemble(state)
    first_misses = cache.misses
    second_prompt = assembler.assemble(state)

    assert second_prompt == first_prompt
    assert cache.hits > 0
    assert cache.misses == first_misses

    state.metadata["files_read"] = {str(tmp_path / "changed.py")}
    changed_prompt = assembler.assemble(state)

    assert "changed.py" in changed_prompt
    assert changed_prompt != first_prompt
    assert cache.misses > first_misses


def test_registry_visible_descriptors_drive_schema_and_prompt_sections() -> None:
    state = RuntimeState()
    registry = ToolRegistry(
        [
            make_descriptor("allowed", prompt="allowed prompt"),
            make_descriptor("denied", prompt="denied prompt"),
            make_descriptor("disabled", prompt="disabled prompt"),
        ],
        denied_tools=("denied",),
    )
    state.metadata["disabled_tools"] = {"disabled"}

    schemas = registry.tool_schemas(state)
    prompts = registry.tool_prompt_sections(state)
    visible_names = [descriptor.name for descriptor in registry.visible_descriptors(state)]

    assert visible_names == ["allowed"]
    assert [schema["function"]["name"] for schema in schemas] == ["allowed"]
    assert prompts == ("allowed prompt",)


def test_context_engine_default_prompt_is_dynamic(tmp_path: Path) -> None:
    state = RuntimeState()
    message_store = MessageStore(
        transcript_root=tmp_path / ".onecode",
        session_id=state.session_id,
        cwd=tmp_path,
        flush_interval_seconds=60,
    )
    engine = ContextEngine(message_store)

    snapshot = engine.build_for_model(state)

    assert "# Identity\n" in snapshot.system_prompt
    assert "# Behavior Rules\n" in snapshot.system_prompt
    assert snapshot.system_prompt.strip()
