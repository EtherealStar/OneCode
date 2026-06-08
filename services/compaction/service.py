"""Context compaction preparer used before model calls."""

from __future__ import annotations

from copy import deepcopy
import re
import uuid
from typing import TYPE_CHECKING, Any, Protocol

from core.runtime_state import RuntimeState
from services.compaction.session_memory import SessionMemoryStore
from services.compaction.token_estimator import estimate_messages_tokens
from services.compaction.types import (
    CompactionConfig,
    CompactionResult,
    CompactionTrigger,
)
from services.context.message_store import MessageStore
from services.context.projector import ContextProjector
from services.context.snapshot import PreparedContext
from services.hooks import HookEvent, HookRegistry
from services.model.types import ProviderError
from services.observability import TraceRecorder
from services.subagents.types import SubagentRequest
from utils.toolResultStorage import ToolResultStorage

if TYPE_CHECKING:
    from services.subagents.runner import SubagentRunner

MICROCOMPACT_PLACEHOLDER = (
    "[Old tool result content cleared. Re-read the referenced file or rerun the "
    "tool if exact output is needed.]"
)


class SubagentRunnerProtocol(Protocol):
    async def run(self, request: SubagentRequest): ...


class SessionMemoryExtractorProtocol(Protocol):
    async def wait_for_current_extraction(self, state: RuntimeState) -> None: ...


class ContextCompactionService:
    def __init__(
        self,
        *,
        config: CompactionConfig | None = None,
        message_store: MessageStore | None = None,
        session_memory_store: SessionMemoryStore | None = None,
        session_memory_extractor: SessionMemoryExtractorProtocol | None = None,
        result_store: ToolResultStorage | None = None,
        subagent_runner: SubagentRunnerProtocol | None = None,
        hooks: HookRegistry | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.config = config or CompactionConfig()
        self._message_store = message_store
        self._session_memory_store = session_memory_store
        self._session_memory_extractor = session_memory_extractor
        self._result_store = result_store
        self._subagent_runner = subagent_runner
        self._hooks = hooks or HookRegistry()
        self._trace_recorder = trace_recorder or TraceRecorder.noop()

    def bind_runtime(
        self,
        *,
        message_store: MessageStore | None = None,
        session_memory_store: SessionMemoryStore | None = None,
        session_memory_extractor: SessionMemoryExtractorProtocol | None = None,
        result_store: ToolResultStorage | None = None,
        subagent_runner: SubagentRunnerProtocol | None = None,
    ) -> None:
        if message_store is not None:
            self._message_store = message_store
        if session_memory_store is not None:
            self._session_memory_store = session_memory_store
        if session_memory_extractor is not None:
            self._session_memory_extractor = session_memory_extractor
        if result_store is not None:
            self._result_store = result_store
        if subagent_runner is not None:
            self._subagent_runner = subagent_runner

    def bind_session_memory_extractor(
        self,
        session_memory_extractor: SessionMemoryExtractorProtocol | None,
    ) -> None:
        self._session_memory_extractor = session_memory_extractor

    async def prepare(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> PreparedContext:
        result = await self.prepare_for_model(messages, state)
        if (
            result.token_after >= self.config.auto_compact_threshold_tokens
            and state.metadata.get("query_source") != "compact"
        ):
            compacted = await self.maybe_auto_compact(messages, state)
            if compacted is not None:
                return _prepared_context_from_result(compacted)
        return _prepared_context_from_result(result)

    async def prepare_for_model(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> CompactionResult:
        """Run cheap model-visible projection without rewriting MessageStore."""

        token_before = estimate_messages_tokens(messages)
        projected = self._apply_tool_result_budget(messages)
        projected = self._snip(projected)
        projected = self._microcompact(projected)
        token_after = estimate_messages_tokens(projected)
        result = CompactionResult(
            trigger=CompactionTrigger.MICRO,
            messages=projected,
            token_before=token_before,
            token_after=token_after,
            transcript_refs=_stored_result_refs(projected),
            metadata={
                "cheap_pipeline": True,
                "auto_compact_threshold_tokens": self.config.auto_compact_threshold_tokens,
            },
        )
        state.metadata["last_compaction"] = {
            "trigger": result.trigger.value,
            "token_before": token_before,
            "token_after": token_after,
            "message_count_before": len(messages),
            "message_count_after": len(projected),
        }
        self._trace_recorder.event(
            "compact_prepare",
            {
                "trigger": result.trigger.value,
                "token_before": token_before,
                "token_after": token_after,
                "message_count_before": len(messages),
                "message_count_after": len(projected),
            },
        )
        return result

    async def maybe_auto_compact(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> CompactionResult | None:
        prepared = await self.prepare_for_model(messages, state)
        if prepared.token_after < self.config.auto_compact_threshold_tokens:
            return None
        if _auto_compact_failures(state) >= self.config.max_consecutive_auto_compact_failures:
            self._trace_recorder.event(
                "compact_auto_decision",
                {
                    "decision": "skipped_circuit_open",
                    "failure_count": _auto_compact_failures(state),
                    "token_after": prepared.token_after,
                    "threshold": self.config.auto_compact_threshold_tokens,
                },
            )
            return None
        self._trace_recorder.event(
            "compact_auto_decision",
            {
                "decision": "compact",
                "token_after": prepared.token_after,
                "threshold": self.config.auto_compact_threshold_tokens,
            },
        )
        try:
            memory_result = await self._try_session_memory_compact(
                messages,
                state,
                trigger=CompactionTrigger.AUTO_SESSION_MEMORY,
            )
            if memory_result is not None:
                _reset_auto_compact_failures(state)
                return memory_result
            full_result = await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.AUTO_FULL,
            )
            _reset_auto_compact_failures(state)
            return full_result
        except Exception as exc:
            _increment_auto_compact_failures(state)
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.AUTO_FULL,
                error=exc,
                token_before=prepared.token_after,
                message_count=len(messages),
            )
            return None

    async def manual_compact(
        self,
        state: RuntimeState,
        *,
        focus: str | None = None,
    ) -> CompactionResult:
        messages = self._active_messages()
        try:
            return await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.MANUAL,
                focus=focus,
            )
        except Exception as exc:
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.MANUAL,
                error=exc,
                token_before=estimate_messages_tokens(messages),
                message_count=len(messages),
            )
            raise

    async def reactive_compact(
        self,
        state: RuntimeState,
        *,
        error: ProviderError,
    ) -> CompactionResult:
        messages = self._active_messages()
        try:
            memory_result = await self._try_session_memory_compact(
                messages,
                state,
                trigger=CompactionTrigger.REACTIVE,
            )
            if memory_result is not None:
                return memory_result
            return await self._full_compact(
                messages,
                state,
                trigger=CompactionTrigger.REACTIVE,
                focus=error.error_type or error.message,
            )
        except Exception as exc:
            await self._compact_failed(
                state,
                trigger=CompactionTrigger.REACTIVE,
                error=exc,
                token_before=estimate_messages_tokens(messages),
                message_count=len(messages),
            )
            raise

    def _active_messages(self) -> tuple[dict[str, Any], ...]:
        if self._message_store is None:
            raise RuntimeError("compaction requires a bound MessageStore")
        return self._message_store.current_messages()

    async def _try_session_memory_compact(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
    ) -> CompactionResult | None:
        if self._session_memory_store is None:
            return None
        if self._session_memory_extractor is not None:
            await self._session_memory_extractor.wait_for_current_extraction(state)
        memory = self._session_memory_store.read()
        if memory is None or memory.is_empty:
            return None
        token_before = estimate_messages_tokens(messages)
        boundary_id = _boundary_id()
        hook_metadata = await self._pre_compact(
            state,
            trigger=trigger,
            token_before=token_before,
            message_count=len(messages),
        )
        tail = self._recent_tail_for_session_memory(messages)
        compacted = _compact_messages(
            trigger=trigger,
            boundary_id=boundary_id,
            summary=memory.content,
            tail=tail,
            source="session_memory",
        )
        token_after = estimate_messages_tokens(compacted)
        if token_after >= self.config.auto_compact_threshold_tokens:
            return None
        stored = self._replace_active_messages(
            compacted,
            trigger=trigger,
            boundary_id=boundary_id,
            metadata={**hook_metadata, "source": "session_memory"},
        )
        result = CompactionResult(
            trigger=trigger,
            messages=tuple(stored),
            token_before=token_before,
            token_after=token_after,
            metadata={
                "boundary_id": boundary_id,
                "source": "session_memory",
                "session_memory_path": str(self._session_memory_store.path),
            },
        )
        await self._post_compact(state, result, messages_before=len(messages))
        return result

    async def _full_compact(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
        focus: str | None = None,
    ) -> CompactionResult:
        if self._subagent_runner is None:
            raise RuntimeError("full compact requires a SubagentRunner")
        token_before = estimate_messages_tokens(messages)
        boundary_id = _boundary_id()
        hook_metadata = await self._pre_compact(
            state,
            trigger=trigger,
            token_before=token_before,
            message_count=len(messages),
            focus=focus,
        )
        prompt = _compact_prompt(
            focus=focus,
            extra_instructions=hook_metadata.get("summary_instructions"),
        )
        self._trace_recorder.event(
            "compact_start",
            {
                "trigger": trigger.value,
                "token_before": token_before,
                "message_count": len(messages),
            },
        )
        result = await self._subagent_runner.run(
            SubagentRequest(
                prompt=prompt,
                subagent_type=None,
                parent_session_id=state.session_id,
                parent_tool_call_id=f"compact-{boundary_id}",
                metadata={"query_source": "compact", "trigger": trigger.value},
            )
        )
        if result.is_error:
            raise RuntimeError(result.final_text)
        summary = _extract_summary(result.final_text)
        tail = ContextProjector(max_messages=min(20, self.config.snip_max_messages)).project(
            messages,
        )
        compacted = _compact_messages(
            trigger=trigger,
            boundary_id=boundary_id,
            summary=summary,
            tail=tail,
            source="full",
        )
        token_after = estimate_messages_tokens(compacted)
        stored = self._replace_active_messages(
            compacted,
            trigger=trigger,
            boundary_id=boundary_id,
            metadata={**hook_metadata, "source": "full"},
        )
        compaction_result = CompactionResult(
            trigger=trigger,
            messages=tuple(stored),
            token_before=token_before,
            token_after=token_after,
            metadata={
                "boundary_id": boundary_id,
                "source": "full",
                "subagent_session_id": result.session_id,
            },
        )
        await self._post_compact(state, compaction_result, messages_before=len(messages))
        return compaction_result

    def _recent_tail_for_session_memory(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        selected: list[dict[str, Any]] = []
        token_count = 0
        text_count = 0
        for message in reversed(messages):
            projected = deepcopy(message)
            next_tokens = estimate_messages_tokens([projected])
            if (
                selected
                and token_count + next_tokens > self.config.session_memory_max_tokens
                and text_count >= self.config.session_memory_min_text_messages
            ):
                break
            selected.insert(0, projected)
            token_count += next_tokens
            if message.get("role") in {"user", "assistant"}:
                text_count += 1
            if (
                token_count >= self.config.session_memory_min_tokens
                and text_count >= self.config.session_memory_min_text_messages
            ):
                break
        tail = tuple(selected)
        start_index = max(0, len(messages) - len(tail))
        adjusted = ContextProjector().adjust_start_index_to_preserve_tool_pairs(
            messages,
            start_index,
        )
        return tuple(deepcopy(message) for message in messages[adjusted:])

    def _replace_active_messages(
        self,
        messages: tuple[dict[str, Any], ...],
        *,
        trigger: CompactionTrigger,
        boundary_id: str,
        metadata: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if self._message_store is None:
            raise RuntimeError("compaction requires a bound MessageStore")
        stored = self._message_store.replace_messages_for_compaction(
            messages,
            reason=trigger.value,
            metadata={"boundary_id": boundary_id, **metadata},
        )
        self._trace_recorder.event(
            "compact_completed",
            {
                "trigger": trigger.value,
                "boundary_id": boundary_id,
                "messages_after": len(stored),
                "token_after": estimate_messages_tokens(stored),
            },
        )
        return stored

    async def _pre_compact(
        self,
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
        token_before: int,
        message_count: int,
        focus: str | None = None,
    ) -> dict[str, Any]:
        transcript_path = None
        if self._message_store is not None:
            transcript_path = self._message_store.transcript_store.messages_path
        result = await self._hooks.run(
            HookEvent.PRE_COMPACT,
            {
                "trigger": trigger.value,
                "token_before": token_before,
                "message_count": message_count,
                "transcript_path": transcript_path,
                "session_id": state.session_id,
                "turn_count": state.turn_count,
                "focus": focus,
            },
        )
        return dict(result.metadata)

    async def _post_compact(
        self,
        state: RuntimeState,
        result: CompactionResult,
        *,
        messages_before: int,
    ) -> None:
        state.metadata["last_compaction"] = {
            "trigger": result.trigger.value,
            "token_before": result.token_before,
            "token_after": result.token_after,
            "message_count_before": messages_before,
            "message_count_after": len(result.messages),
            "boundary_id": result.metadata.get("boundary_id"),
        }
        await self._hooks.run(
            HookEvent.POST_COMPACT,
            {
                "trigger": result.trigger.value,
                "token_before": result.token_before,
                "token_after": result.token_after,
                "messages_before": messages_before,
                "messages_after": len(result.messages),
                "boundary_id": result.metadata.get("boundary_id"),
                "session_id": state.session_id,
            },
        )

    async def _compact_failed(
        self,
        state: RuntimeState,
        *,
        trigger: CompactionTrigger,
        error: Exception,
        token_before: int,
        message_count: int,
    ) -> None:
        self._trace_recorder.event(
            "compact_failed",
            {
                "trigger": trigger.value,
                "error_type": type(error).__name__,
                "token_before": token_before,
                "message_count": message_count,
                "failure_count": _auto_compact_failures(state),
            },
        )
        await self._hooks.run(
            HookEvent.COMPACT_FAILED,
            {
                "trigger": trigger.value,
                "error_type": type(error).__name__,
                "token_before": token_before,
                "message_count": message_count,
                "session_id": state.session_id,
            },
        )

    def _apply_tool_result_budget(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        projected: list[dict[str, Any]] = []
        stored_count = 0
        for message in messages:
            next_message = deepcopy(message)
            if next_message.get("role") != "tool_result":
                projected.append(next_message)
                continue
            content = next_message.get("content")
            if not isinstance(content, str):
                projected.append(next_message)
                continue
            if len(content) <= self.config.tool_result_budget_chars:
                projected.append(next_message)
                continue

            preview = content[: self.config.tool_result_preview_chars]
            metadata = dict(next_message.get("metadata") or {})
            metadata.update(
                {
                    "result_truncated": True,
                    "original_size_chars": len(content),
                    "max_result_size_chars": self.config.tool_result_budget_chars,
                }
            )
            if self._result_store is not None:
                ref = self._result_store.persist_tool_result(
                    tool_call_id=str(next_message.get("tool_call_id", "")),
                    tool_name=str(next_message.get("tool_name", "")),
                    content=content,
                )
                next_message["content"] = self._result_store.format_model_reference(
                    ref,
                    preview=preview,
                )
                metadata.update(
                    self._result_store.stored_result_metadata(
                        ref,
                        max_result_size_chars=self.config.tool_result_budget_chars,
                    )
                )
                stored_count += 1
            else:
                next_message["content"] = preview
            next_message["metadata"] = metadata
            projected.append(next_message)

        self._trace_recorder.event(
            "compact_result_budget",
            {"stored_result_count": stored_count, "message_count": len(messages)},
        )
        return tuple(projected)

    def _snip(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        if len(messages) <= self.config.snip_max_messages:
            return tuple(deepcopy(message) for message in messages)
        return ContextProjector(max_messages=self.config.snip_max_messages).project(messages)

    def _microcompact(
        self,
        messages: tuple[dict[str, Any], ...],
    ) -> tuple[dict[str, Any], ...]:
        tool_result_indexes = [
            index
            for index, message in enumerate(messages)
            if message.get("role") == "tool_result"
        ]
        if self.config.microcompact_keep_recent <= 0:
            keep: set[int] = set()
        else:
            keep = set(tool_result_indexes[-self.config.microcompact_keep_recent :])
        compacted: list[dict[str, Any]] = []
        compacted_count = 0
        for index, message in enumerate(messages):
            next_message = deepcopy(message)
            if index in keep or next_message.get("role") != "tool_result":
                compacted.append(next_message)
                continue
            metadata = dict(next_message.get("metadata") or {})
            if metadata.get("result_stored") is True:
                compacted.append(next_message)
                continue
            content = next_message.get("content")
            if isinstance(content, str) and content:
                metadata.update(
                    {
                        "microcompacted": True,
                        "original_size_chars": len(content),
                    }
                )
                next_message["content"] = MICROCOMPACT_PLACEHOLDER
                next_message["metadata"] = metadata
                compacted_count += 1
            compacted.append(next_message)

        self._trace_recorder.event(
            "compact_micro",
            {
                "microcompacted_count": compacted_count,
                "keep_recent": self.config.microcompact_keep_recent,
            },
        )
        return tuple(compacted)


def _stored_result_refs(messages: tuple[dict[str, Any], ...]) -> tuple[str, ...]:
    refs: list[str] = []
    for message in messages:
        metadata = message.get("metadata")
        if not isinstance(metadata, dict):
            continue
        ref = metadata.get("stored_result_path")
        if isinstance(ref, str):
            refs.append(ref)
    return tuple(refs)


def _prepared_context_from_result(result: CompactionResult) -> PreparedContext:
    return PreparedContext(
        messages=result.messages,
        usage_hints={
            "compaction_trigger": result.trigger.value,
            "token_before": result.token_before,
            "token_after": result.token_after,
            **result.metadata,
        },
        transcript_refs=result.transcript_refs,
    )


def _compact_messages(
    *,
    trigger: CompactionTrigger,
    boundary_id: str,
    summary: str,
    tail: tuple[dict[str, Any], ...],
    source: str,
) -> tuple[dict[str, Any], ...]:
    boundary = {
        "role": "user",
        "content": (
            f"[Compact boundary: trigger={trigger.value}, "
            f"boundary_id={boundary_id}, source={source}]"
        ),
        "metadata": {
            "is_compact_boundary": True,
            "compact_boundary_id": boundary_id,
            "compact_trigger": trigger.value,
            "compact_source": source,
        },
    }
    summary_message = {
        "role": "user",
        "content": (
            "This session is being continued from a compacted context.\n\n"
            "Summary:\n"
            f"{summary.strip()}"
        ),
        "metadata": {
            "is_compact_summary": True,
            "compact_boundary_id": boundary_id,
            "compact_trigger": trigger.value,
            "compact_source": source,
        },
    }
    return (boundary, summary_message, *tuple(deepcopy(message) for message in tail))


def _compact_prompt(
    *,
    focus: str | None,
    extra_instructions: Any,
) -> str:
    instructions = str(extra_instructions).strip() if extra_instructions else ""
    focus_text = focus.strip() if isinstance(focus, str) and focus.strip() else ""
    lines = [
        "Compact the current session into a concise continuation summary.",
        "Do not call tools. Return only text.",
        "Include these sections:",
        "- User Requests And Intent",
        "- Key Technical Concepts",
        "- Files And Code",
        "- Errors And Fixes",
        "- Problem Solving Process",
        "- All User Messages Summary",
        "- Pending Work",
        "- Current Work",
        "- Next Step",
    ]
    if focus_text:
        lines.append(f"Focus: {focus_text}")
    if instructions:
        lines.append(f"Additional summary instructions: {instructions}")
    lines.append("Wrap the final summary in <summary>...</summary> if useful.")
    return "\n".join(lines)


def _extract_summary(text: str) -> str:
    without_analysis = re.sub(
        r"<analysis>.*?</analysis>",
        "",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()
    match = re.search(
        r"<summary>(.*?)</summary>",
        without_analysis,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return without_analysis


def _boundary_id() -> str:
    return uuid.uuid4().hex[:12]


def _auto_compact_failures(state: RuntimeState) -> int:
    value = state.metadata.get("auto_compact_failure_count", 0)
    return value if isinstance(value, int) else 0


def _increment_auto_compact_failures(state: RuntimeState) -> None:
    state.metadata["auto_compact_failure_count"] = _auto_compact_failures(state) + 1


def _reset_auto_compact_failures(state: RuntimeState) -> None:
    state.metadata["auto_compact_failure_count"] = 0
