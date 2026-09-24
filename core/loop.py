"""轻量 Agent 生命周期循环。

在流式输出方面，主循环的唯一职责是在 provider 事件发生时实时转发给
调用方（并最终到达 CLI）。我们特意不在回放前将整次尝试收集到缓冲区中，
因为那样会破坏实时流式体验。

单次模型尝试进行期间，循环维护的状态包括：

- completed_message：最终的 message_completed 事件，以便循环在尝试结束时
  持久化 assistant 消息、推导工具调用，并执行输出中断恢复。
- seen_tool_calls：在尝试期间完成解析的工具调用，以便尝试结束后仍可获取
  （我们不会将原始 JSON 实时行内转发给 CLI；只有在 JSON 解析成功后工具调用才
  进入 ready 状态，以便对其执行进行门禁控制）。

其余所有事件均实时转发给调用方：content_delta 立即转为 assistant_delta；
tool_call_completed 立即转为 tool_call_ready；tool_call_delta 按原样转发，
以便 CLI 能够展示流式工具输入状态，同时不提前将其视为可执行状态。

恢复语义（重试、最大输出限制升级、反应式压缩）直接对调用方可见，而非隐藏在
缓冲区后。_run_loop_async 中的 on_retry 回调会记录流转事件，以便 UI 能够显示
provider 流中断并重试，而不是静默回退用户已经看到的文本。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any, Protocol

from core.context_engine import ContextEngine
from core.runtime_state import RuntimeState
from core.stream_events import AgentEvent, mint_assistant_call_id
from core.transitions import TransitionReason
from services.context.current_model_context import CurrentModelContext
from services.context.message_store import MessageStore
from services.context.run_facts import InterruptedRunFacts, RunFactsAccumulator
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
    ) -> Any: ...


class SessionMemoryUpdaterProtocol(Protocol):
    async def update_after_turn(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> None: ...


class SessionMemoryExtractorProtocol(Protocol):
    async def maybe_extract_after_model_response(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        assistant_message: dict[str, Any],
        tool_calls: tuple[Any, ...],
        usage: Any | None = None,
    ) -> None: ...


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
        self._run_facts: RunFactsAccumulator | None = None

    def snapshot_run_facts(
        self, *, status: str | None = None
    ) -> InterruptedRunFacts | None:
        """返回当前前台运行事实的不可变快照。

        主循环会在运行正常完成前持续维护这些事实，因此即使结果尚未追加到
        消息存储中，中断时也能根据实际发生的情况完成状态终态化。
        """

        if self._run_facts is None:
            return None
        return self._run_facts.freeze(status=status)

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
            user_message_uuid = self.message_store.last_record_uuid or ""
            self._begin_run_facts()
            if attachments is not None:
                self.message_store.append_attachments(attachments)
            # 携带刚追加的用户消息的持久化标识，以便应用层将实时草稿与
            # 稳定记录关联起来，而无需根据文本或位置进行推断。
            yield AgentEvent(
                type="interaction_started",
                metadata={"user_message_uuid": user_message_uuid},
            )
            try:
                async for event in self._run_loop_async():
                    yield event
            except BaseException:
                self._mark_run_interrupted()
                raise

    async def continue_stream(self) -> AsyncIterator[AgentEvent]:
        """从已经预置到消息存储中的消息继续执行。"""

        with self.trace_recorder.span(
            "interaction",
            {"continued_from_seeded_messages": True},
        ):
            self._begin_run_facts()
            yield AgentEvent(type="interaction_started")
            try:
                async for event in self._run_loop_async():
                    yield event
            except BaseException:
                self._mark_run_interrupted()
                raise

    def _begin_run_facts(self) -> None:
        accumulator = RunFactsAccumulator(session_id=self.state.session_id)
        accumulator.user_prompt_uuid = self.message_store.last_record_uuid
        self._run_facts = accumulator

    def _mark_run_interrupted(self) -> None:
        if self._run_facts is not None:
            self._run_facts.status = "interrupted"

    def _complete_run_facts(self) -> None:
        self._run_facts = None

    def _make_on_retry(
        self,
        retry_events: list[AgentEvent],
        turn_index: int,
        call_id: str,
        stream_state: dict[str, bool],
    ) -> Callable[[ProviderError, RetryDecision], Awaitable[None]]:
        """构造本轮的重试回调，避免在循环内闭包捕获会变化的循环变量。"""

        async def on_retry(error: ProviderError, decision: RetryDecision) -> None:
            self.state.set_transition(TransitionReason.RATE_LIMIT_RETRY)
            self._record_transition(TransitionReason.RATE_LIMIT_RETRY)
            retry_events.append(
                AgentEvent(
                    type="transition",
                    transition=TransitionReason.RATE_LIMIT_RETRY.value,
                    metadata={
                        "model_turn_index": turn_index,
                        "assistant_call_id": call_id,
                        "attempt": decision.attempt,
                        "max_retries": decision.max_retries,
                        "delay_seconds": decision.delay_seconds,
                        "error_type": error.error_type,
                        "partial_output_visible": stream_state["any_text"],
                    },
                )
            )

        return on_retry

    async def _run_loop_async(self) -> AsyncIterator[AgentEvent]:
        while True:
            self.state.turn_count += 1
            # 为当前 turn 内即将开始的模型调用准备稳定归属 ID。同一
            # turn 可能因为工具调用而触发多次模型调用，每次都要分配
            # 新的 model_turn_index 和 assistant_call_id，让
            # checkpoint 渲染能区分不同 assistant message。
            model_turn_index = self._next_model_turn_index()
            assistant_call_id = mint_assistant_call_id(
                self.state.session_id,
                self.state.turn_count,
                model_turn_index,
            )
            if self._run_facts is not None:
                self._run_facts.begin_model_call(
                    assistant_call_id,
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
                self._complete_run_facts()
                return

            # 主循环保持轻量：上下文、提示词和工具 schema 都交给
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
            # 用于驱动尝试后逻辑的局部状态。流式模型事件本身不会收集在此处，
            # 而是实时转发给调用方。
            completed_message: ModelStreamEvent | None = None
            pending_retry_events: list[AgentEvent] = []
            # 跟踪 provider 已完成的工具调用，以便在尝试完成后由执行器运行。
            completed_tool_calls: tuple = ()
            # 使用可变容器承载本轮的流式状态：on_retry 必须读取重试前是否已有
            # 部分输出，按值绑定会冻结为 False，因此这里按引用共享。
            stream_state: dict[str, bool] = {"any_text": False}
            on_retry = self._make_on_retry(
                pending_retry_events,
                model_turn_index,
                assistant_call_id,
                stream_state,
            )

            try:
                with self.trace_recorder.span(
                    "model_call",
                    model_attributes,
                ) as model_span:
                    async for model_event in self.model_retry_runner.stream(
                        lambda snapshot=snapshot: self.model_client.stream(snapshot),
                        on_retry=on_retry,
                    ):
                        # 实时转发每个事件。我们不追加到缓冲区；
                        # 由调用方（CLI）决定如何渲染。
                        if model_event.type == "content_delta":
                            stream_state["any_text"] = True
                            if self._run_facts is not None:
                                self._run_facts.add_text(model_event.text or "")
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
                            if (
                                self._run_facts is not None
                                and model_event.tool_call is not None
                            ):
                                self._run_facts.declare(
                                    model_event.tool_call.id,
                                    model_event.tool_call.name,
                                )
                            yield AgentEvent(
                                type="tool_call_ready",
                                metadata={
                                    "model_turn_index": model_turn_index,
                                    "assistant_call_id": assistant_call_id,
                                    "tool_call": model_event.tool_call,
                                },
                            )
                            # 跟踪最新的工具调用，以便在尝试结束后执行。
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
                            if self._run_facts is not None:
                                self._run_facts.set_assistant_message(
                                    model_event.assistant_message
                                )
                                self._run_facts.declare_many(completed_tool_calls)
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
                            # 尝试已完成，向外抛出运行器在各次尝试之间收集的重试流转事件。
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

            # 在尝试成功后向外抛出排队的重试流转事件。退避逻辑仍由重试运行器负责，
            # 此处仅负责状态可见性。
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
                completed_message,
                assistant_call_id=assistant_call_id,
                model_turn_index=model_turn_index,
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

            # 文本增量和 tool_call_ready 事件已经实时转发。现在只需将最终的
            # assistant 消息记录到消息存储中，并向钩子通知完成事件。
            self.message_store.append_assistant(
                completed_message.assistant_message,
                assistant_call_id=assistant_call_id,
                model_turn_index=model_turn_index,
            )
            if self._run_facts is not None:
                self._run_facts.note_assistant_record(
                    self.message_store.last_record_uuid
                )
            tool_calls = completed_tool_calls or self._event_tool_calls(
                completed_message
            )
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
                self.message_store.append_tool_results(
                    result_blocks,
                    assistant_call_id=assistant_call_id,
                    model_turn_index=model_turn_index,
                )
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
            self._complete_run_facts()
            return

    async def _execute_tools(
        self,
        tool_calls: tuple,
        results: list[ToolExecutionResult],
        model_turn_index: int,
        assistant_call_id: str,
    ) -> AsyncIterator[AgentEvent]:
        # 工具事件必须携带同一份稳定归属 metadata，reducer 才能把
        # tool_result 归属到产生该工具调用的 assistant message。
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
                if self._run_facts is not None:
                    self._run_facts.add_result(update.result)
                yield AgentEvent(
                    type="tool_result",
                    result=update.result,
                    metadata=dict(attribution),
                )

    def _next_model_turn_index(self) -> int:
        """返回当前会话的下一个 model_turn_index。

        同一个 turn_count 可能触发多次模型调用（例如 assistant 声明工具后，
        主循环回到 while 循环顶部再次调用模型）。此处使用 state.metadata
        维护会话内严格递增的整数，确保每次新模型调用都有新的归属 ID，
        使旧模型调用的 checkpoint 及其 assistant 文本与工具事件能够准确绑定。
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
        *,
        assistant_call_id: str | None = None,
        model_turn_index: int | None = None,
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
            assistant_message = completed_message.assistant_message
            if assistant_message is None:
                return None
            # 续写恢复会持久化被截断的 assistant 消息（用户已经看到该内容），
            # 随后追加简短的用户提示词以便模型继续生成。
            self.message_store.append_assistant(
                assistant_message,
                assistant_call_id=assistant_call_id,
                model_turn_index=model_turn_index,
            )
            if self._run_facts is not None:
                self._run_facts.note_assistant_record(
                    self.message_store.last_record_uuid
                )
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
        """发布与 provider 无关的采样后事件及会话记忆钩子。"""

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
