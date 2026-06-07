"""Thin agent lifecycle loop."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

from core.context_engine import ContextEngine
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent
from core.transitions import TransitionReason
from services.context.message_store import MessageStore
from services.context.current_model_context import CurrentModelContext
from services.hooks import HookEvent, HookRegistry
from services.model.client import ModelClient
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.observability import TraceRecorder
from services.tools.executor import ToolExecutor
from services.tools.types import ToolExecutionResult


class ReactiveCompactor(Protocol):
    async def reactive_compact(
        self,
        state: RuntimeState,
        *,
        error: ProviderError,
    ) -> Any:
        ...


class SessionMemoryUpdaterProtocol(Protocol):
    async def update_after_turn(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> None:
        ...


class SessionMemoryExtractorProtocol(Protocol):
    async def maybe_extract_after_model_response(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        assistant_message: dict[str, Any],
        tool_calls: tuple[Any, ...],
        usage: Any | None = None,
    ) -> None:
        ...


class AgentLoop:
    def __init__(
        self,
        *,
        state: RuntimeState,
        message_store: MessageStore,
        context_engine: ContextEngine,
        model_client: ModelClient,
        tool_executor: ToolExecutor,
        trace_recorder: TraceRecorder | None = None,
        current_model_context: CurrentModelContext | None = None,
        hooks: HookRegistry | None = None,
        compaction_service: ReactiveCompactor | None = None,
        session_memory_extractor: SessionMemoryExtractorProtocol | None = None,
        session_memory_updater: SessionMemoryUpdaterProtocol | None = None,
    ) -> None:
        self.state = state
        self.message_store = message_store
        self.message_store.bind_session(self.state.session_id)
        self.context_engine = context_engine
        self.model_client = model_client
        self.tool_executor = tool_executor
        self.trace_recorder = trace_recorder or TraceRecorder.noop(
            self.state.session_id
        )
        self.current_model_context = current_model_context
        self.hooks = hooks or HookRegistry()
        self.compaction_service = compaction_service
        self.session_memory_extractor = session_memory_extractor
        self.session_memory_updater = session_memory_updater

    async def stream(
        self,
        prompt: str,
        *,
        attachments: Iterable[dict[str, Any]] | None = None,
    ) -> AsyncIterator[AgentEvent]:
        with self.trace_recorder.span(
            "interaction",
            {"user_prompt_length": len(prompt)},
        ):
            await self.hooks.run(
                HookEvent.USER_PROMPT_SUBMIT,
                {
                    "prompt_length": len(prompt),
                    "session_id": self.state.session_id,
                    "turn_count": self.state.turn_count,
                },
            )
            self.message_store.append_user(prompt)
            if attachments is not None:
                self.message_store.append_attachments(attachments)
            yield AgentEvent(type="interaction_started")
            async for event in self._run_loop_async():
                yield event

    async def continue_stream(self) -> AsyncIterator[AgentEvent]:
        """Continue from messages already seeded into the message store."""

        with self.trace_recorder.span(
            "interaction",
            {"continued_from_seeded_messages": True},
        ):
            yield AgentEvent(type="interaction_started")
            async for event in self._run_loop_async():
                yield event

    async def _run_loop_async(self) -> AsyncIterator[AgentEvent]:
        while True:
            self.state.turn_count += 1
            if self.state.turn_count > self.state.max_turns:
                self.state.set_transition(TransitionReason.MAX_TURNS)
                self._record_transition(TransitionReason.MAX_TURNS)
                text = "Stopped: maximum turn count reached."
                yield AgentEvent(
                    type="transition",
                    transition=TransitionReason.MAX_TURNS.value,
                )
                yield AgentEvent(type="completed", text=text)
                return

            # 主循环保持薄：上下文、prompt 和工具 schema 都交给
            # ContextEngine 每轮重建，以反映最新运行时状态。
            with self.trace_recorder.span(
                "context_prepare",
                {"turn_count": self.state.turn_count},
            ) as context_span:
                snapshot = await self.context_engine.build_for_model(self.state)
                if self.current_model_context is not None:
                    self.current_model_context.snapshot = snapshot
                context_span.end(
                    {
                        "message_count": len(snapshot.messages),
                        "tool_schema_count": len(snapshot.tool_schemas),
                        "has_system_prompt": bool(snapshot.system_prompt),
                    }
                )

            model_attributes = self._model_attributes()
            model_attributes["turn_count"] = self.state.turn_count
            completed_message: ModelStreamEvent | None = None
            try:
                with self.trace_recorder.span(
                    "model_call",
                    model_attributes,
                ) as model_span:
                    async for model_event in self.model_client.stream(snapshot):
                        if model_event.type == "content_delta":
                            yield AgentEvent(
                                type="assistant_delta",
                                text=model_event.text,
                                metadata=model_event.metadata,
                            )
                        elif model_event.type == "tool_call_completed":
                            yield AgentEvent(
                                type="tool_call_ready",
                                metadata={
                                    "tool_call": model_event.tool_call,
                                },
                            )
                        elif model_event.type == "message_completed":
                            completed_message = model_event
                            tool_calls = self._event_tool_calls(model_event)
                            end_attributes = {
                                "tool_call_count": len(tool_calls),
                                "stop_reason": model_event.stop_reason,
                                "output_interrupted": model_event.output_interrupted,
                            }
                            if model_event.usage is not None:
                                end_attributes.update(
                                    {
                                        "input_tokens": model_event.usage.input_tokens,
                                        "output_tokens": model_event.usage.output_tokens,
                                        "cache_read_input_tokens": (
                                            model_event.usage.cache_read_input_tokens
                                        ),
                                        "cache_creation_input_tokens": (
                                            model_event.usage.cache_creation_input_tokens
                                        ),
                                    }
                                )
                            model_span.end(end_attributes)
            except ProviderError as exc:
                self.trace_recorder.event(
                    "model_call_error",
                    {
                        "provider_id": exc.provider_id,
                        "status_code": exc.status_code,
                        "error_type": exc.error_type,
                        "retryable": exc.retryable,
                    },
                )
                if await self._try_reactive_compact(exc):
                    yield AgentEvent(
                        type="transition",
                        transition=TransitionReason.REACTIVE_COMPACT_RETRY.value,
                    )
                    continue
                raise

            if completed_message is None or completed_message.assistant_message is None:
                raise ProviderError(
                    "Provider stream did not complete a message.",
                    error_type="invalid_response",
                )

            if completed_message.usage is not None:
                self.state.add_usage(completed_message.usage)

            self.message_store.append_assistant(completed_message.assistant_message)
            tool_calls = self._event_tool_calls(completed_message)
            yield AgentEvent(
                type="assistant_message_completed",
                text=completed_message.final_text,
                metadata={
                    "stop_reason": completed_message.stop_reason,
                    "output_interrupted": completed_message.output_interrupted,
                },
            )
            await self._after_assistant_message_completed(
                completed_message,
                tool_calls,
            )

            # 是否继续执行工具取决于实际 tool_calls，而不是 provider 私有的
            # stop reason 字段。
            if tool_calls:
                result_blocks: list[ToolExecutionResult] = []
                async for event in self._execute_tools(tool_calls, result_blocks):
                    yield event
                self.message_store.append_tool_results(result_blocks)
                followup_messages = tuple(
                    message
                    for result in result_blocks
                    if not result.is_error
                    for message in result.followup_messages
                )
                if followup_messages:
                    self.message_store.append_attachments(followup_messages)
                self.state.set_transition(TransitionReason.TOOL_USE)
                self._record_transition(TransitionReason.TOOL_USE)
                yield AgentEvent(
                    type="transition",
                    transition=TransitionReason.TOOL_USE.value,
                )
                continue

            await self._after_turn_stopped(completed_message, tool_calls)
            self.state.set_transition(TransitionReason.COMPLETED)
            self._record_transition(TransitionReason.COMPLETED)
            yield AgentEvent(
                type="transition",
                transition=TransitionReason.COMPLETED.value,
            )
            yield AgentEvent(type="completed", text=completed_message.final_text)
            return

    async def _execute_tools(
        self,
        tool_calls: tuple,
        results: list[ToolExecutionResult],
    ) -> AsyncIterator[AgentEvent]:
        async for update in self.tool_executor.execute(tool_calls, self.state):
            if update.type == "started":
                yield AgentEvent(
                    type="tool_started",
                    metadata={
                        "tool_call_id": update.tool_call_id,
                        "tool_name": update.tool_name,
                        **update.metadata,
                    },
                )
            elif update.type == "progress":
                yield AgentEvent(
                    type="tool_progress",
                    text=update.content,
                    metadata={
                        "tool_call_id": update.tool_call_id,
                        "tool_name": update.tool_name,
                        **update.metadata,
                    },
                )
            elif update.result is not None:
                results.append(update.result)
                yield AgentEvent(type="tool_result", result=update.result)

    def _event_tool_calls(
        self,
        event: ModelStreamEvent,
    ) -> tuple:
        tool_calls = event.metadata.get("tool_calls", ())
        return tool_calls if isinstance(tool_calls, tuple) else ()

    def _record_transition(self, transition: TransitionReason) -> None:
        self.trace_recorder.event(
            "transition",
            {
                "transition": transition.value,
                "turn_count": self.state.turn_count,
            },
        )

    def _model_attributes(self) -> dict[str, object]:
        config = getattr(self.model_client, "config", None)
        return {
            "provider_id": getattr(config, "provider_id", None),
            "model": getattr(config, "model", None),
        }

    async def _try_reactive_compact(self, error: ProviderError) -> bool:
        if error.error_type != "context_limit_exceeded":
            return False
        if self.compaction_service is None:
            return False
        if self.state.has_attempted_reactive_compact:
            return False
        self.state.has_attempted_reactive_compact = True
        self.state.set_transition(TransitionReason.REACTIVE_COMPACT_RETRY)
        self._record_transition(TransitionReason.REACTIVE_COMPACT_RETRY)
        self.trace_recorder.event(
            "reactive_compact_retry",
            {
                "error_type": error.error_type,
                "status_code": error.status_code,
                "turn_count": self.state.turn_count,
            },
        )
        await self.compaction_service.reactive_compact(self.state, error=error)
        return True

    async def _after_assistant_message_completed(
        self,
        completed_message: ModelStreamEvent,
        tool_calls: tuple[Any, ...],
    ) -> None:
        """Publish the provider-neutral post-sampling event and memory hook."""

        messages = self.message_store.current_messages()
        await self.hooks.run(
            HookEvent.ASSISTANT_MESSAGE_COMPLETED,
            {
                "assistant_message": completed_message.assistant_message,
                "final_text": completed_message.final_text,
                "tool_calls": tool_calls,
                "usage": completed_message.usage,
                "state": self.state,
                "messages": messages,
            },
        )
        if self.session_memory_extractor is not None:
            await self.session_memory_extractor.maybe_extract_after_model_response(
                messages,
                self.state,
                assistant_message=completed_message.assistant_message or {},
                tool_calls=tool_calls,
                usage=completed_message.usage,
            )
            return
        if self.session_memory_updater is not None and not tool_calls:
            await self.session_memory_updater.update_after_turn(messages, self.state)

    async def _after_turn_stopped(
        self,
        completed_message: ModelStreamEvent,
        tool_calls: tuple[Any, ...],
    ) -> None:
        messages = self.message_store.current_messages()
        await self.hooks.run(
            HookEvent.TURN_STOPPED,
            {
                "assistant_message": completed_message.assistant_message,
                "final_text": completed_message.final_text,
                "tool_calls": tool_calls,
                "usage": completed_message.usage,
                "state": self.state,
                "messages": messages,
                "query_source": self.state.metadata.get("query_source"),
                "long_term_memory_writes": self.state.metadata.get(
                    "long_term_memory_writes",
                    (),
                ),
            },
        )
