"""Thin agent lifecycle loop.

The loop's only job with respect to streaming is to forward provider
events to the caller (and ultimately the CLI) **as they happen**. We
deliberately do **not** collect the full attempt into a buffer before
replaying it: that would defeat real-time streaming.

State the loop keeps while a single model attempt is in flight:

- ``completed_message`` — the final ``message_completed`` event so the
  loop can persist the assistant message, derive tool calls, and run
  output-interruption recovery once the attempt finishes.
- ``seen_tool_calls`` — tool calls that were completed during the
  attempt, so we still have them after the attempt ends (we don't
  forward their JSON to the CLI inline; tool calls are only "ready"
  when JSON parses, and we still want to gate execution on that).

Everything else is forwarded to the caller live. ``content_delta``
becomes ``assistant_delta`` *immediately*; ``tool_call_completed``
becomes ``tool_call_ready`` *immediately*; ``tool_call_delta`` is
forwarded as ``tool_call_delta`` so the CLI can show streaming tool
status without pretending the tool is runnable yet.

Recovery semantics (retry, max-output, reactive compact) are now
visible to the caller instead of being hidden by a buffer. The
``on_retry`` callback in :meth:`_run_loop_async` records a transition
event so the UI can show ``provider stream interrupted; retrying``
rather than silently rewinding text the user has already seen.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Any, Protocol

from core.context_engine import ContextEngine
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent, mint_assistant_call_id
from core.transitions import TransitionReason
from services.context.message_store import MessageStore
from services.context.current_model_context import CurrentModelContext
from services.hooks import HookEvent, HookRegistry
from services.model.client import ModelClient
from services.model.retry import ModelRetryRunner, RetryDecision
from services.model.stream import ModelStreamEvent
from services.model.types import ProviderError
from services.observability import ErrorLogRecorder, TraceRecorder
from services.tools.executor import ToolExecutor
from services.tools.types import ToolExecutionResult

ESCALATED_MAX_OUTPUT_TOKENS = 64000
MAX_OUTPUT_RECOVERY_RETRIES = 3
CONTINUATION_PROMPT = (
    "Output token limit hit. Resume directly; no apology, no recap of what you "
    "were doing. Pick up mid-thought if that is where the cut happened. Break "
    "remaining work into smaller pieces."
)


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
        model_retry_runner: ModelRetryRunner | None = None,
        error_log_recorder: ErrorLogRecorder | None = None,
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
        self.error_log_recorder = error_log_recorder or ErrorLogRecorder.noop(
            self.state.session_id
        )
        self.model_retry_runner = model_retry_runner or ModelRetryRunner(
            trace_recorder=self.trace_recorder,
            error_log_recorder=self.error_log_recorder,
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
            # 为当前 turn 内即将开始的模型调用准备稳定归属 ID。同一
            # turn 可能因为工具调用而触发多次模型调用,每次都要分配
            # 新的 ``model_turn_index`` 和 ``assistant_call_id``,让
            # checkpoint 渲染能区分不同 assistant message。
            model_turn_index = self._next_model_turn_index()
            assistant_call_id = mint_assistant_call_id(
                self.state.session_id,
                self.state.turn_count,
                model_turn_index,
            )
            if (
                self.state.max_turns is not None
                and self.state.turn_count > self.state.max_turns
            ):
                self.state.set_transition(TransitionReason.MAX_TURNS)
                self._record_transition(TransitionReason.MAX_TURNS)
                text = "Stopped: maximum turn count reached."
                yield AgentEvent(
                    type="transition",
                    transition=TransitionReason.MAX_TURNS.value,
                    metadata={
                        "model_turn_index": model_turn_index,
                        "assistant_call_id": assistant_call_id,
                    },
                )
                yield AgentEvent(
                    type="completed",
                    text=text,
                    metadata={
                        "model_turn_index": model_turn_index,
                        "assistant_call_id": assistant_call_id,
                    },
                )
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
            # Local state used to drive post-attempt logic. The streaming
            # model events themselves are NOT collected here — we forward
            # them to the caller live.
            completed_message: ModelStreamEvent | None = None
            pending_retry_events: list[AgentEvent] = []
            # We still keep track of tool calls the provider finished so
            # the executor can run them after the attempt completes.
            completed_tool_calls: tuple = ()
            streamed_any_text = False
            streamed_any_tool_call = False

            async def on_retry(
                error: ProviderError,
                decision: RetryDecision,
            ) -> None:
                self.state.set_transition(TransitionReason.RATE_LIMIT_RETRY)
                self._record_transition(TransitionReason.RATE_LIMIT_RETRY)
                pending_retry_events.append(
                    AgentEvent(
                        type="transition",
                        transition=TransitionReason.RATE_LIMIT_RETRY.value,
                        metadata={
                            "model_turn_index": model_turn_index,
                            "assistant_call_id": assistant_call_id,
                            "attempt": decision.attempt,
                            "max_retries": decision.max_retries,
                            "delay_seconds": decision.delay_seconds,
                            "error_type": error.error_type,
                            "partial_output_visible": streamed_any_text,
                        },
                    )
                )

            try:
                with self.trace_recorder.span(
                    "model_call",
                    model_attributes,
                ) as model_span:
                    async for model_event in self.model_retry_runner.stream(
                        lambda: self.model_client.stream(snapshot),
                        on_retry=on_retry,
                    ):
                        # Forward every event live. We do not append to a
                        # buffer; the caller (CLI) decides how to render.
                        if model_event.type == "content_delta":
                            streamed_any_text = True
                            yield AgentEvent(
                                type="assistant_delta",
                                text=model_event.text,
                                metadata={
                                    **model_event.metadata,
                                    "model_turn_index": model_turn_index,
                                    "assistant_call_id": assistant_call_id,
                                },
                            )
                            continue
                        if model_event.type == "tool_call_delta":
                            streamed_any_tool_call = True
                            yield AgentEvent(
                                type="tool_call_delta",
                                metadata={
                                    **model_event.metadata,
                                    "model_turn_index": model_turn_index,
                                    "assistant_call_id": assistant_call_id,
                                },
                            )
                            continue
                        if model_event.type == "tool_call_completed":
                            yield AgentEvent(
                                type="tool_call_ready",
                                metadata={
                                    "model_turn_index": model_turn_index,
                                    "assistant_call_id": assistant_call_id,
                                    "tool_call": model_event.tool_call,
                                },
                            )
                            # Track the latest tool calls so we can
                            # execute them after the attempt closes.
                            if (
                                completed_message is not None
                                and "tool_calls" in model_event.metadata
                            ):
                                completed_tool_calls = tuple(
                                    model_event.metadata.get("tool_calls") or ()
                                )
                            continue
                        if model_event.type == "message_completed":
                            completed_message = model_event
                            completed_tool_calls = self._event_tool_calls(model_event)
                            end_attributes = {
                                "tool_call_count": len(completed_tool_calls),
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
                            # Now that the attempt is complete, surface
                            # any retry transitions that the runner
                            # collected between attempts.
                            continue
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
                        metadata={
                            "model_turn_index": model_turn_index,
                            "assistant_call_id": assistant_call_id,
                        },
                    )
                    continue
                self.error_log_recorder.record_error(
                    exc,
                    source="agent_loop_model_call",
                    attributes={"turn_count": self.state.turn_count},
                )
                raise
            except Exception as exc:
                self.error_log_recorder.record_error(
                    exc,
                    source="agent_loop_model_call",
                    attributes={"turn_count": self.state.turn_count},
                )
                raise

            # Surface queued retry transitions after the successful
            # attempt. The retry runner still owns backoff; we only
            # expose the visibility here.
            for retry_event in pending_retry_events:
                yield retry_event

            if completed_message is None or completed_message.assistant_message is None:
                error = ProviderError(
                    "Provider stream did not complete a message.",
                    error_type="invalid_response",
                )
                self.error_log_recorder.record_error(
                    error,
                    source="agent_loop_model_call",
                    attributes={"turn_count": self.state.turn_count},
                )
                raise error

            if completed_message.usage is not None:
                self.state.add_usage(completed_message.usage)

            output_recovery = self._prepare_output_interruption_recovery(
                completed_message
            )
            if output_recovery is not None:
                yield AgentEvent(
                    type="transition",
                    transition=output_recovery.value,
                    metadata={
                        "model_turn_index": model_turn_index,
                        "assistant_call_id": assistant_call_id,
                    },
                )
                continue

            # The text deltas and tool_call_ready events have already
            # been forwarded live. We now just record the final
            # assistant message into the message store and announce the
            # completion to hooks.
            self.message_store.append_assistant(completed_message.assistant_message)
            tool_calls = completed_tool_calls or self._event_tool_calls(completed_message)
            yield AgentEvent(
                type="assistant_message_completed",
                text=completed_message.final_text,
                metadata={
                    "model_turn_index": model_turn_index,
                    "assistant_call_id": assistant_call_id,
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
                async for event in self._execute_tools(
                    tool_calls, result_blocks, model_turn_index, assistant_call_id
                ):
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
                    metadata={
                        "model_turn_index": model_turn_index,
                        "assistant_call_id": assistant_call_id,
                    },
                )
                continue

            await self._after_turn_stopped(completed_message, tool_calls)
            self.state.set_transition(TransitionReason.COMPLETED)
            self._record_transition(TransitionReason.COMPLETED)
            yield AgentEvent(
                type="transition",
                transition=TransitionReason.COMPLETED.value,
                metadata={
                    "model_turn_index": model_turn_index,
                    "assistant_call_id": assistant_call_id,
                },
            )
            yield AgentEvent(
                type="completed",
                text=completed_message.final_text,
                metadata={
                    "model_turn_index": model_turn_index,
                    "assistant_call_id": assistant_call_id,
                },
            )
            return

    async def _execute_tools(
        self,
        tool_calls: tuple,
        results: list[ToolExecutionResult],
        model_turn_index: int,
        assistant_call_id: str,
    ) -> AsyncIterator[AgentEvent]:
        # 工具事件必须携带同一份稳定归属 metadata,reducer 才
        # 能把 tool_result 归到产生该工具调用的 assistant message。
        attribution: dict[str, Any] = {
            "model_turn_index": model_turn_index,
            "assistant_call_id": assistant_call_id,
        }
        async for update in self.tool_executor.execute(tool_calls, self.state):
            if update.type == "started":
                yield AgentEvent(
                    type="tool_started",
                    metadata={
                        **attribution,
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
                        **attribution,
                        "tool_call_id": update.tool_call_id,
                        "tool_name": update.tool_name,
                        **update.metadata,
                    },
                )
            elif update.result is not None:
                results.append(update.result)
                yield AgentEvent(
                    type="tool_result",
                    result=update.result,
                    metadata=dict(attribution),
                )

    def _next_model_turn_index(self) -> int:
        """Return the next ``model_turn_index`` for the current session.

        One ``turn_count`` 可能触发多次模型调用（assistant 声明工具
        后,主循环回到 while 顶端再次调用模型)。这里用
        ``state.metadata`` 维护一个 session 内严格递增的整数,确保
        每次新模型调用都有新的归属 id,旧模型调用的 checkpoint 与
        它的 assistant text / tool 事件能继续被准确绑定。
        """

        counter = self.state.metadata.get("model_turn_counter")
        if not isinstance(counter, int):
            counter = 0
        counter += 1
        self.state.metadata["model_turn_counter"] = counter
        return counter

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

    def _prepare_output_interruption_recovery(
        self,
        completed_message: ModelStreamEvent,
    ) -> TransitionReason | None:
        if not completed_message.output_interrupted:
            return None

        if not self.state.has_escalated_max_output_tokens:
            self.state.has_escalated_max_output_tokens = True
            overrides = dict(self.state.metadata.get("model_request_overrides") or {})
            overrides["max_output_tokens"] = ESCALATED_MAX_OUTPUT_TOKENS
            self.state.metadata["model_request_overrides"] = overrides
            self.state.set_transition(TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE)
            self._record_transition(TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE)
            self.trace_recorder.event(
                "max_output_tokens_escalate",
                {
                    "max_output_tokens": ESCALATED_MAX_OUTPUT_TOKENS,
                    "turn_count": self.state.turn_count,
                    "stop_reason": completed_message.stop_reason,
                },
            )
            return TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE

        if self.state.max_output_recovery_count < MAX_OUTPUT_RECOVERY_RETRIES:
            # Continuation recovery persists the truncated assistant
            # (the user has already seen it) and follows it with a
            # terse user prompt so the model can resume.
            self.message_store.append_assistant(completed_message.assistant_message)
            self.message_store.append_user(CONTINUATION_PROMPT)
            self.state.max_output_recovery_count += 1
            self.state.set_transition(TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY)
            self._record_transition(TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY)
            self.trace_recorder.event(
                "max_output_tokens_recovery",
                {
                    "recovery_count": self.state.max_output_recovery_count,
                    "max_retries": MAX_OUTPUT_RECOVERY_RETRIES,
                    "turn_count": self.state.turn_count,
                    "stop_reason": completed_message.stop_reason,
                },
            )
            return TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY

        self.trace_recorder.event(
            "max_output_tokens_recovery_exhausted",
            {
                "recovery_count": self.state.max_output_recovery_count,
                "max_retries": MAX_OUTPUT_RECOVERY_RETRIES,
                "turn_count": self.state.turn_count,
                "stop_reason": completed_message.stop_reason,
            },
        )
        return None

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
