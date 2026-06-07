"""Context reconstruction boundary for each model call."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, Awaitable, Iterable, Protocol

from core.runtime_state import RuntimeState
from prompts.assembler import DynamicPromptAssembler
from services.context.message_store import MessageStore
from services.context.snapshot import ContextSnapshot, PreparedContext


class ContextPreparer(Protocol):
    def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> (
        Iterable[dict[str, Any]]
        | PreparedContext
        | Awaitable[Iterable[dict[str, Any]] | PreparedContext]
    ):
        ...


class PromptAssembler(Protocol):
    def assemble(self, state: RuntimeState) -> str:
        ...


class ToolSchemaProvider(Protocol):
    def tool_schemas(self, state: RuntimeState) -> Iterable[dict[str, Any]]:
        ...


class NoOpContextPreparer:
    def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> tuple[dict[str, Any], ...]:
        return messages


class StaticPromptAssembler:
    """Testing helper for callers that need a fixed prompt."""

    def __init__(self, system_prompt: str = "") -> None:
        self._system_prompt = system_prompt

    def assemble(self, state: RuntimeState) -> str:
        return self._system_prompt


class EmptyToolSchemaProvider:
    def tool_schemas(self, state: RuntimeState) -> tuple[dict[str, Any], ...]:
        return ()


class ContextEngine:
    def __init__(
        self,
        message_store: MessageStore,
        prompt_assembler: PromptAssembler | None = None,
        tool_schema_provider: ToolSchemaProvider | None = None,
        context_preparer: ContextPreparer | None = None,
    ) -> None:
        self._message_store = message_store
        self._prompt_assembler = prompt_assembler or DynamicPromptAssembler(Path.cwd())
        self._tool_schema_provider = tool_schema_provider or EmptyToolSchemaProvider()
        self._context_preparer = context_preparer or NoOpContextPreparer()

    async def build_for_model(self, state: RuntimeState) -> ContextSnapshot:
        current_messages = self._message_store.current_messages()
        # preparer 是未来 compaction/projector 的边界；当前通常只是透传，
        # 但调用方仍应统一经过这个 awaitable 入口。
        prepared = self._context_preparer.prepare(current_messages, state)
        if inspect.isawaitable(prepared):
            prepared = await prepared
        usage_hints: dict[str, Any] = {}
        transcript_refs: tuple[str, ...] = ()
        if isinstance(prepared, PreparedContext):
            prepared_messages = tuple(prepared.messages)
            usage_hints = dict(prepared.usage_hints)
            transcript_refs = tuple(prepared.transcript_refs)
        else:
            prepared_messages = tuple(prepared)
        request_overrides = _safe_request_overrides(
            state.metadata.get("model_request_overrides")
        )
        if request_overrides:
            usage_hints["request_overrides"] = request_overrides
        system_prompt = self._prompt_assembler.assemble(state)
        tool_schemas = tuple(self._tool_schema_provider.tool_schemas(state))

        return ContextSnapshot(
            system_prompt=system_prompt,
            messages=prepared_messages,
            tool_schemas=tool_schemas,
            usage_hints=usage_hints,
            transcript_refs=transcript_refs,
            transition=(
                state.last_transition.value if state.last_transition is not None else None
            ),
        )


def _safe_request_overrides(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    overrides: dict[str, Any] = {}
    max_output_tokens = value.get("max_output_tokens")
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        overrides["max_output_tokens"] = max_output_tokens
    return overrides
