"""The SessionController: the application-layer session Module.

It owns one runtime, a single foreground turn worker, the pending input FIFO,
interaction coordination, and the ordered snapshot/update stream. UI adapters
(TUI, batch) submit intent and observe updates; they never assemble the agent
loop or drain a private queue.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from application.history import HistoryRecord, load_conversation_history
from application.interactions import (
    InteractionCoordinator,
    PermissionPromptAdapter,
    UserQuestionPromptAdapter,
)
from application.types import (
    AssistantDelta,
    CancelResult,
    DetailLoaded,
    DetailRef,
    DetailResult,
    InteractionAnswer,
    InteractionKind,
    InteractionRequested,
    InteractionResolved,
    MessageCommitted,
    QueueChanged,
    QueueItem,
    ResponseResult,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunState,
    SessionSnapshot,
    SessionUpdate,
    SnapshotUpdate,
    StatusChanged,
    SubmissionReceipt,
    ToolRunState,
    ToolUpdate,
    UsageChanged,
    UserMessageCommitted,
    WithdrawalResult,
)
from core.stream_events import AgentEvent
from services.model.types import ModelUsage
from services.observability import ErrorLogRecorder
from services.plans import build_plan_attachments_for_state

_CLOSED = object()
_SUBSCRIBER_QUEUE_SIZE = 256


def _tool_input(tool: Any, metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Extract declared tool arguments for display without guessing."""

    value = getattr(tool, "input", None)
    if isinstance(value, Mapping):
        return dict(value)
    declared = metadata.get("tool_input")
    if isinstance(declared, Mapping):
        return dict(declared)
    return {}


@dataclass
class _Subscriber:
    queue: "asyncio.Queue[object]" = field(
        default_factory=lambda: asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_SIZE)
    )


@dataclass
class _ActiveRun:
    input_id: str
    run_id: str
    text: str
    assistant_text: str = ""
    last_completed_text: str = ""
    assistant_call_id: str | None = None
    model_turn_index: int | None = None
    user_message_uuid: str = ""
    tools: dict[str, ToolRunState] = field(default_factory=dict)

    def to_run_state(self) -> RunState:
        return RunState(
            active=True,
            input_id=self.input_id,
            text=self.text,
            assistant_text=self.assistant_text,
            assistant_call_id=self.assistant_call_id,
            model_turn_index=self.model_turn_index,
            tools=tuple(self.tools.values()),
            status="running",
            run_id=self.run_id,
        )


class SessionController:
    def __init__(
        self,
        runtime: Any,
        *,
        error_log_recorder: ErrorLogRecorder | None = None,
    ) -> None:
        self._runtime = runtime
        self._error_log_recorder = (
            error_log_recorder
            or getattr(runtime, "error_log_recorder", None)
            or ErrorLogRecorder.noop()
        )
        session_id = runtime.state.session_id
        self._generation = 1
        self._sequence = 0
        self._subscribers: list[_Subscriber] = []
        self._queue: deque[QueueItem] = deque()
        self._pending_attachments: list[dict[str, Any]] = []
        self._paused = False
        self._active: _ActiveRun | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._bootstrap_task: asyncio.Task[None] | None = None
        self._work_available = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self.command_lock = asyncio.Lock()
        self._closed = False
        self._initialized = False
        self._status = "initializing"
        self._last_cancel: CancelResult | None = None
        self._interactions = InteractionCoordinator(
            session_id=session_id,
            on_request=self._on_interaction_request,
            on_resolved=self._on_interaction_resolved,
        )
        self._install_adapters()

    # --- lifecycle --------------------------------------------------------

    @property
    def runtime(self) -> Any:
        return self._runtime

    @property
    def session_id(self) -> str:
        return self._runtime.state.session_id

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def configured(self) -> bool:
        return bool(getattr(self._runtime, "configured", True))

    def _install_adapters(self) -> None:
        permission_adapter = PermissionPromptAdapter(self._interactions)
        question_adapter = UserQuestionPromptAdapter(self._interactions)
        prompter = getattr(self._runtime, "permission_prompter", None)
        if prompter is not None and hasattr(prompter, "target"):
            prompter.target = permission_adapter
        else:
            try:
                self._runtime.permission_prompter = permission_adapter
            except Exception:
                pass
        question_prompter = getattr(self._runtime, "user_question_prompter", None)
        if question_prompter is not None and hasattr(question_prompter, "target"):
            question_prompter.target = question_adapter
        else:
            try:
                self._runtime.user_question_prompter = question_adapter
            except Exception:
                pass

    async def __aenter__(self) -> "SessionController":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> "SessionController":
        if self._closed:
            raise RuntimeError("SessionController is closed")
        if self._bootstrap_task is None:
            self._bootstrap_task = asyncio.create_task(
                self._bootstrap(), name=f"session-bootstrap-{self.session_id}"
            )
        self._ensure_worker()
        return self

    async def _bootstrap(self) -> None:
        try:
            if self._closed:
                return
            self._initialized = True
            self._set_status("ready" if self.configured else "unconfigured")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            self._record_error(exc, source="session_bootstrap")
            self._initialized = True
            self._set_status("error")

    # --- observation ------------------------------------------------------

    def snapshot(self) -> SessionSnapshot:
        return self._build_snapshot()

    async def watch(self) -> AsyncIterator[SessionUpdate]:
        subscriber = _Subscriber()
        snapshot = self._build_snapshot()
        self._subscribers.append(subscriber)
        try:
            yield SnapshotUpdate(snapshot=snapshot)
            while True:
                item = await subscriber.queue.get()
                if item is _CLOSED:
                    return
                yield item  # type: ignore[misc]
        finally:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    # --- submits and queue ------------------------------------------------

    async def submit(
        self,
        text: str,
        *,
        attachments: tuple[dict[str, Any], ...] = (),
        kind: str = "prompt",
    ) -> SubmissionReceipt:
        stripped = text.strip()
        if not stripped:
            return SubmissionReceipt(
                input_id="", session_id=self.session_id, status="rejected", reason="empty"
            )
        if self._closed:
            return SubmissionReceipt(
                input_id="", session_id=self.session_id, status="rejected", reason="closed"
            )
        if kind == "prompt" and not self.configured:
            return SubmissionReceipt(
                input_id="",
                session_id=self.session_id,
                status="rejected",
                reason="not_configured",
            )
        item = QueueItem(
            input_id=str(uuid4()),
            text=stripped,
            kind="prompt" if kind == "prompt" else "command",
            attachments=tuple(attachments),
        )
        start_now = self._active is None and not self._paused and not self._queue
        self._queue.append(item)
        self._emit_queue_changed()
        self._work_available.set()
        return SubmissionReceipt(
            input_id=item.input_id,
            session_id=self.session_id,
            status="started" if start_now else "queued",
            queue_length=len(self._queue),
        )

    async def withdraw(self, input_id: str) -> WithdrawalResult:
        if self._active is not None and self._active.input_id == input_id:
            return WithdrawalResult(
                input_id=input_id, withdrawn=False, reason="already_started"
            )
        for index, item in enumerate(self._queue):
            if item.input_id == input_id:
                del self._queue[index]
                self._emit_queue_changed()
                return WithdrawalResult(
                    input_id=input_id, withdrawn=True, text=item.text
                )
        return WithdrawalResult(input_id=input_id, withdrawn=False, reason="not_found")

    async def resume_queue(self) -> None:
        if self._closed or not self._paused:
            return
        # A failed interrupt cleanup must not be skipped silently: the on-disk
        # transcript is not known-good, so refuse to run the next input.
        if self._last_cancel is not None and self._last_cancel.cleanup_success is False:
            self._emit(
                lambda generation, sequence: StatusChanged(
                    generation=generation,
                    sequence=sequence,
                    status="cleanup_failed",
                    configured=self.configured,
                )
            )
            return
        self._paused = False
        self._emit_queue_changed()
        self._work_available.set()

    async def await_safe_point(self) -> None:
        """Block until no foreground turn is running.

        Mutation commands use this so a status change does not race model or
        tool execution. View commands never wait.
        """

        if self._active is not None:
            await self._idle.wait()

    async def cancel_active(self) -> CancelResult:
        if self._active is None:
            return CancelResult(cancelled=False, reason="no_active_run")
        task = self._worker_task
        if task is None or task.done():
            return CancelResult(cancelled=False, reason="no_worker")
        self._last_cancel = None
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            self._record_error(exc, source="session_cancel")
        if not self._closed:
            self._ensure_worker()
        return self._last_cancel or CancelResult(cancelled=True)

    # --- interactions -----------------------------------------------------

    async def respond(
        self, request_id: str, kind: Any = None, payload: Any = None
    ) -> ResponseResult:
        """Answer a request by id.

        Accepts either an :class:`InteractionAnswer` (whose ``kind`` and
        ``payload`` are used) or an explicit ``kind`` + ``payload`` pair.
        """

        if isinstance(kind, InteractionAnswer):
            return await self._interactions.resolve(
                request_id, kind.kind, kind.payload
            )
        return await self._interactions.resolve(request_id, kind, payload)

    async def request_interaction(
        self,
        kind: InteractionKind,
        payload: Any,
        *,
        options: tuple[Any, ...] = (),
        run_id: str | None = None,
    ) -> InteractionAnswer:
        return await self._interactions.request(
            kind, payload, run_id=run_id, options=options
        )

    def active_interaction(self) -> Any:
        return self._interactions.active_request()

    # --- details ----------------------------------------------------------

    async def load_detail(self, ref: DetailRef) -> DetailResult:
        result = self._read_detail(ref)
        self._emit(
            lambda generation, sequence: DetailLoaded(
                generation=generation,
                sequence=sequence,
                ref=ref,
                result=result,
            )
        )
        return result

    def _read_detail(self, ref: DetailRef) -> DetailResult:
        if ref.session_id != self.session_id:
            return DetailResult(success=False, error="stale_session")
        if ref.kind != "tool_result":
            return DetailResult(success=False, error="unsupported_ref")
        relative_path = ref.relative_path
        if not relative_path:
            return DetailResult(success=False, missing=True, error="no_reference")
        store = getattr(
            getattr(self._runtime, "message_store", None), "transcript_store", None
        )
        storage = getattr(store, "tool_result_storage", None)
        if storage is None:
            return DetailResult(success=False, error="detail_unavailable")
        try:
            text = storage.read_result(relative_path)
        except FileNotFoundError:
            return DetailResult(success=False, missing=True, error="missing_artifact")
        except OSError as exc:
            return DetailResult(success=False, error=str(exc))
        return DetailResult(success=True, text=text, externalized=True)

    # --- close ------------------------------------------------------------

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._interactions.close()
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.queue.put_nowait(_CLOSED)
            except asyncio.QueueFull:
                pass
        if self._bootstrap_task is not None and not self._bootstrap_task.done():
            self._bootstrap_task.cancel()
            try:
                await self._bootstrap_task
            except (asyncio.CancelledError, Exception):
                pass
        task = self._worker_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - defensive
                self._record_error(exc, source="session_close")
        self._worker_task = None
        self._queue.clear()
        self._flush_runtime()
        await self._close_runtime_resources()

    def _flush_runtime(self) -> None:
        message_store = getattr(self._runtime, "message_store", None)
        if message_store is not None:
            try:
                message_store.flush_transcript()
            except Exception as exc:  # pragma: no cover - defensive
                self._record_error(exc, source="session_close_flush")
        for name in ("trace_recorder", "error_log_recorder"):
            recorder = getattr(self._runtime, name, None)
            if recorder is not None:
                try:
                    recorder.flush()
                except Exception:
                    pass

    async def _close_runtime_resources(self) -> None:
        mcp_manager = getattr(self._runtime, "mcp_manager", None)
        if mcp_manager is not None:
            try:
                await mcp_manager.close_all()
            except Exception as exc:  # pragma: no cover - defensive
                self._record_error(exc, source="session_close_mcp")

    # --- worker -----------------------------------------------------------

    def _ensure_worker(self) -> None:
        if self._closed:
            return
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(
                self._worker_main(), name=f"session-worker-{self.session_id}"
            )

    def _pop_runnable(self) -> QueueItem | None:
        if self._closed or self._paused or self._active is not None or not self._queue:
            return None
        item = self._queue.popleft()
        self._emit_queue_changed()
        return item

    async def _worker_main(self) -> None:
        while True:
            self._work_available.clear()
            item = self._pop_runnable()
            if item is None:
                if self._closed:
                    return
                await self._work_available.wait()
                continue
            try:
                await self._run_turn(item)
            except asyncio.CancelledError:
                return

    async def _run_turn(self, item: QueueItem) -> None:
        run = _ActiveRun(input_id=item.input_id, run_id=uuid4().hex, text=item.text)
        self._active = run
        self._last_cancel = None
        self._idle.clear()
        self._emit(
            lambda generation, sequence: RunStarted(
                generation=generation,
                sequence=sequence,
                input_id=item.input_id,
                text=item.text,
            )
        )
        try:
            try:
                attachments = await self._collect_attachments(item)
                async for event in self._runtime.loop.stream(
                    item.text, attachments=attachments
                ):
                    self._handle_event(run, event)
            except asyncio.CancelledError:
                self._finalize_cancel(run)
                raise
            except Exception as exc:
                self._active = None
                self._record_error(exc, source="session_turn")
                self._paused = True
                self._emit(
                    lambda generation, sequence: RunFailed(
                        generation=generation,
                        sequence=sequence,
                        input_id=item.input_id,
                        error=str(exc),
                        recoverable=True,
                    )
                )
                self._emit_queue_changed()
                return
            self._active = None
            self._emit(
                lambda generation, sequence: RunCompleted(
                    generation=generation,
                    sequence=sequence,
                    input_id=item.input_id,
                    text=run.last_completed_text or run.assistant_text,
                )
            )
        finally:
            self._idle.set()

    def _finalize_cancel(self, run: _ActiveRun) -> None:
        facts = None
        snapshot_facts = getattr(self._runtime.loop, "snapshot_run_facts", None)
        if callable(snapshot_facts):
            try:
                facts = snapshot_facts(status="interrupted")
            except Exception as exc:  # pragma: no cover - defensive
                self._record_error(exc, source="session_cancel_snapshot")
        cleanup_success = True
        error: str | None = None
        message_store = getattr(self._runtime, "message_store", None)
        if facts is not None and message_store is not None and hasattr(
            message_store, "finalize_interrupted_run"
        ):
            try:
                result = message_store.finalize_interrupted_run(
                    facts, error_log_recorder=self._error_log_recorder
                )
                cleanup_success = bool(getattr(result, "success", True))
                error = getattr(result, "error", None)
            except Exception as exc:
                cleanup_success = False
                error = str(exc)
                self._record_error(exc, source="session_cancel_cleanup")
        self._interactions.cancel_pending("cancelled")
        self._active = None
        self._paused = True
        self._last_cancel = CancelResult(
            cancelled=True,
            reason="interrupted",
            cleanup_success=cleanup_success,
        )
        self._emit(
            lambda generation, sequence: RunCancelled(
                generation=generation,
                sequence=sequence,
                input_id=run.input_id,
                cleanup_success=cleanup_success,
                error=error,
            )
        )
        self._emit_queue_changed()
        # The cleanup rewrote the transcript: publish the authoritative history
        # so observers replace their projection instead of guessing deletions.
        self._publish_snapshot()

    async def _collect_attachments(
        self, item: QueueItem
    ) -> tuple[dict[str, Any], ...]:
        attachments: list[dict[str, Any]] = list(item.attachments)
        if self._pending_attachments:
            attachments.extend(self._pending_attachments)
            self._pending_attachments.clear()
        collector = getattr(self._runtime, "attachment_collector", None)
        if collector is not None:
            try:
                collected = await collector.collect_for_user_turn(
                    item.text,
                    self._runtime.state,
                    self._runtime.message_store.current_messages(),
                    is_main_thread=True,
                )
                attachments.extend(collected)
            except Exception as exc:
                self._record_error(exc, source="session_attachments")
        plan_store = getattr(self._runtime, "plan_store", None)
        if plan_store is not None:
            try:
                attachments.extend(
                    build_plan_attachments_for_state(self._runtime.state, plan_store)
                )
            except Exception as exc:
                self._record_error(exc, source="session_plan_attachments")
        return tuple(attachments)

    def _handle_event(self, run: _ActiveRun, event: AgentEvent) -> None:
        event_type = event.type
        if event_type == "interaction_started":
            # The loop appends the user message before this event and carries
            # its stable record UUID, so the projection can key the committed
            # user message without guessing from text or position.
            user_uuid = event.metadata.get("user_message_uuid") or ""
            if isinstance(user_uuid, str) and user_uuid:
                run.user_message_uuid = user_uuid
            self._emit(
                lambda generation, sequence: UserMessageCommitted(
                    generation=generation,
                    sequence=sequence,
                    input_id=run.input_id,
                    message_uuid=run.user_message_uuid,
                    text=run.text,
                )
            )
            return
        if event_type == "assistant_delta":
            call_id = event.metadata.get("assistant_call_id")
            if call_id != run.assistant_call_id:
                run.assistant_call_id = call_id
                run.assistant_text = ""
            model_turn_index = event.metadata.get("model_turn_index")
            if isinstance(model_turn_index, int):
                run.model_turn_index = model_turn_index
            run.assistant_text += event.text or ""
            self._emit(
                lambda generation, sequence: AssistantDelta(
                    generation=generation,
                    sequence=sequence,
                    text=event.text or "",
                    assistant_call_id=run.assistant_call_id,
                    model_turn_index=run.model_turn_index,
                )
            )
            return
        if event_type == "assistant_message_completed":
            call_id = event.metadata.get("assistant_call_id") or run.assistant_call_id
            if call_id != run.assistant_call_id:
                run.assistant_call_id = call_id
            model_turn_index = event.metadata.get("model_turn_index")
            if isinstance(model_turn_index, int):
                run.model_turn_index = model_turn_index
            if event.text:
                run.assistant_text = event.text
                run.last_completed_text = event.text
            message_uuid = getattr(
                self._runtime.message_store, "last_record_uuid", None
            ) or ""
            self._emit(
                lambda generation, sequence: MessageCommitted(
                    generation=generation,
                    sequence=sequence,
                    message_uuid=message_uuid,
                    text=event.text or "",
                    assistant_call_id=run.assistant_call_id,
                    model_turn_index=run.model_turn_index,
                )
            )
            usage = getattr(self._runtime.state, "usage", None)
            if isinstance(usage, ModelUsage):
                usage_copy = ModelUsage(
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                    cache_creation_input_tokens=usage.cache_creation_input_tokens,
                )
                self._emit(
                    lambda generation, sequence, usage=usage_copy: UsageChanged(
                        generation=generation,
                        sequence=sequence,
                        usage=usage,
                    )
                )
            return
        if event_type in {"tool_call_ready", "tool_started", "tool_progress"}:
            call_id = event.metadata.get("assistant_call_id") or run.assistant_call_id
            if call_id != run.assistant_call_id:
                run.assistant_call_id = call_id
                run.assistant_text = ""
            model_turn_index = event.metadata.get("model_turn_index")
            if isinstance(model_turn_index, int):
                run.model_turn_index = model_turn_index
            tool = event.metadata.get("tool_call")
            tool_call_id = event.metadata.get("tool_call_id") or getattr(
                tool, "id", ""
            )
            tool_name = event.metadata.get("tool_name") or getattr(
                tool, "name", "unknown_tool"
            )
            tool_input = _tool_input(tool, event.metadata)
            status = {
                "tool_call_ready": "declared",
                "tool_started": "started",
                "tool_progress": "progress",
            }[event_type]
            if not tool_call_id:
                return
            current = run.tools.get(tool_call_id)
            previous_text = current.text if current is not None else ""
            if not tool_input and current is not None:
                tool_input = dict(current.input)
            run.tools[tool_call_id] = ToolRunState(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                status=status,
                text=previous_text,
                input=tool_input,
                metadata=dict(event.metadata),
            )
            self._emit(
                lambda generation, sequence, call_id=run.assistant_call_id, index=run.model_turn_index: ToolUpdate(
                    generation=generation,
                    sequence=sequence,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    status=status,
                    text=event.text or "",
                    assistant_call_id=call_id,
                    model_turn_index=index,
                    input=tool_input,
                )
            )
            return
        if event_type == "tool_result" and event.result is not None:
            result = event.result
            current = run.tools.get(result.tool_call_id)
            tool_input = dict(current.input) if current is not None else {}
            run.tools[result.tool_call_id] = ToolRunState(
                tool_call_id=result.tool_call_id,
                tool_name=result.tool_name,
                status="error" if result.is_error else "completed",
                text=result.content,
                is_error=result.is_error,
                input=tool_input,
                metadata=dict(result.metadata),
            )
            self._emit(
                lambda generation, sequence, call_id=run.assistant_call_id, index=run.model_turn_index: ToolUpdate(
                    generation=generation,
                    sequence=sequence,
                    tool_call_id=result.tool_call_id,
                    tool_name=result.tool_name,
                    status="error" if result.is_error else "completed",
                    text=result.content,
                    is_error=result.is_error,
                    result=result,
                    assistant_call_id=call_id,
                    model_turn_index=index,
                    input=tool_input,
                )
            )
            return
        if event_type == "completed":
            if event.text:
                run.last_completed_text = event.text
            return
        if event_type == "error":
            raise RuntimeError(event.text or "agent loop error")

    # --- commands ---------------------------------------------------------

    async def execute_command(self, line: str) -> Any:
        from application import commands as command_module

        outcome = await command_module.dispatch(self, line)
        return await self._apply_command_outcome(outcome)

    async def _apply_command_outcome(self, outcome: Any) -> Any:
        action = getattr(outcome, "action", None)
        if action == "exit":
            await self.close()
        elif action == "clear":
            await self.clear_session()
        elif action == "resume":
            target = (outcome.data or {}).get("target")
            if target:
                await self.resume_session(target)
        elif action == "submit":
            await self.submit(
                outcome.submit_text or "",
                attachments=tuple(getattr(outcome, "attachments", ()) or ()),
            )
        else:
            attachments = tuple(getattr(outcome, "attachments", ()) or ())
            if attachments:
                self._pending_attachments.extend(attachments)
        if (
            getattr(outcome, "name", "") == "compact"
            and getattr(outcome, "status", "") == "ok"
        ):
            # Compaction rewrote the active chain; republish the authority so
            # projections can rebuild their message tree from real history.
            self._publish_snapshot()
        return outcome

    # --- session rebinding ------------------------------------------------

    async def clear_session(self) -> SessionSnapshot:
        await self._quiesce()
        runtime = self._runtime
        old_session_id = runtime.state.session_id
        runtime.message_store.flush_transcript()
        new_session_id = runtime.state.start_new_session()
        runtime.message_store.clear_for_new_session(new_session_id)
        self._runtime = runtime.with_session(
            state=runtime.state,
            message_store=runtime.message_store,
        )
        self._interactions = InteractionCoordinator(
            session_id=self._runtime.state.session_id,
            on_request=self._on_interaction_request,
            on_resolved=self._on_interaction_resolved,
        )
        self._install_adapters()
        self._generation += 1
        self._queue.clear()
        self._pending_attachments.clear()
        self._last_cancel = None
        self._paused = False
        return self._publish_snapshot()

    async def resume_session(self, target: str) -> SessionSnapshot:
        from application.sessions import restore_runtime_from_target

        await self._quiesce()
        runtime = self._runtime
        restored = restore_runtime_from_target(runtime, target)
        self._runtime = restored
        self._interactions = InteractionCoordinator(
            session_id=restored.state.session_id,
            on_request=self._on_interaction_request,
            on_resolved=self._on_interaction_resolved,
        )
        self._install_adapters()
        self._generation += 1
        self._queue.clear()
        self._pending_attachments.clear()
        self._last_cancel = None
        self._paused = False
        return self._publish_snapshot()

    async def reload_model_config(self) -> bool:
        await self.await_safe_point()
        async with self.command_lock:
            return await self._reload_model_config_locked()

    async def _reload_model_config_locked(self) -> bool:
        try:
            self._runtime = self._runtime.with_model_config()
        except Exception as exc:
            self._record_error(exc, source="session_model_config")
            return False
        self._install_adapters()
        self._emit(
            lambda generation, sequence: StatusChanged(
                generation=generation,
                sequence=sequence,
                status=self._status,
                configured=self.configured,
            )
        )
        return True

    async def _quiesce(self) -> None:
        task = self._worker_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._worker_task = None
        self._active = None
        if not self._closed:
            self._ensure_worker()

    # --- publishing -------------------------------------------------------

    def _build_snapshot(self) -> SessionSnapshot:
        history: tuple[HistoryRecord, ...] = ()
        diagnostics: tuple[str, ...] = ()
        store = getattr(
            getattr(self._runtime, "message_store", None), "transcript_store", None
        )
        if store is not None:
            try:
                loaded = load_conversation_history(store)
                history = loaded.records
                diagnostics = loaded.diagnostics
            except Exception:
                diagnostics = ("history_unavailable",)
        run = self._active.to_run_state() if self._active is not None else RunState()
        return SessionSnapshot(
            session_id=self.session_id,
            generation=self._generation,
            sequence=self._sequence,
            initialized=self._initialized,
            configured=self.configured,
            paused=self._paused,
            status=self._status,
            run=run,
            queue=tuple(self._queue),
            history=history,
            interaction=self._interactions.active_request(),
            pending_interactions=self._interactions.pending_requests(),
            provider_label=getattr(self._runtime, "provider_label", "") or "",
            model=getattr(self._runtime, "model", "") or "",
            usage=self._runtime.state.usage,
            metadata={"workspace": str(getattr(self._runtime, "workspace", ""))},
            diagnostics=diagnostics,
        )

    def _emit(self, build: Any) -> None:
        self._sequence += 1
        update = build(self._generation, self._sequence)
        self._deliver(update)

    def _publish_snapshot(self) -> SessionSnapshot:
        self._sequence += 1
        snapshot = self._build_snapshot()
        self._deliver(SnapshotUpdate(snapshot=snapshot))
        return snapshot

    def _deliver(self, update: SessionUpdate) -> None:
        snapshot: SessionSnapshot | None = None
        for subscriber in tuple(self._subscribers):
            try:
                subscriber.queue.put_nowait(update)
            except asyncio.QueueFull:
                if snapshot is None:
                    snapshot = self._build_snapshot()
                while True:
                    try:
                        subscriber.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                subscriber.queue.put_nowait(SnapshotUpdate(snapshot=snapshot))

    def _emit_queue_changed(self) -> None:
        queue = tuple(self._queue)
        paused = self._paused
        self._emit(
            lambda generation, sequence: QueueChanged(
                generation=generation,
                sequence=sequence,
                queue=queue,
                paused=paused,
            )
        )

    def _set_status(self, status: str) -> None:
        self._status = status
        configured = self.configured
        self._emit(
            lambda generation, sequence: StatusChanged(
                generation=generation,
                sequence=sequence,
                status=status,
                configured=configured,
            )
        )

    def _on_interaction_request(self, request: Any) -> None:
        self._emit(
            lambda generation, sequence: InteractionRequested(
                generation=generation,
                sequence=sequence,
                request=request,
            )
        )

    def _on_interaction_resolved(self, request_id: str, outcome: str) -> None:
        self._emit(
            lambda generation, sequence: InteractionResolved(
                generation=generation,
                sequence=sequence,
                request_id=request_id,
                outcome=outcome,
            )
        )

    def _record_error(self, exc: BaseException, *, source: str) -> None:
        try:
            self._error_log_recorder.record_error(
                exc,
                source=source,
                attributes={"session_id": self.session_id},
            )
        except Exception:
            pass


__all__ = ["SessionController"]
