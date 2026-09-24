from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from ..domain import Message, Role
from ..journal import JournalRecord, JournalRecordType, UserMessagePayload
from ..ports import Cancellation
from ..prompts import IDENTITY
from ..repository import OpenSession, SessionRepository
from ..session import SessionEngine
from ..trace import NullTraceSink, TraceContext, TraceEvent, TraceEventType, TraceSink, sanitize_error
from ..tools.authorization import PermissionDecision, PermissionRequest
from ..tools.todo_write.tool import TodoStore
from ..updates import TodosChanged
from .projection import SessionSnapshot


@dataclass(frozen=True, slots=True)
class AcceptedInput:
    message_id: UUID
    run_id: UUID


UpdateCallback = Callable[[object], Awaitable[None]]


class SessionHandle(Protocol):
    session_id: UUID

    async def submit(self, message: Message) -> AcceptedInput: ...
    async def withdraw(self, message_id: UUID) -> bool: ...
    async def snapshot(self) -> SessionSnapshot: ...
    async def stop(self, reason: str) -> None: ...
    def subscribe(self, callback: UpdateCallback) -> Callable[[], None]: ...


class RuntimeSession:
    """把 SessionEngine 的队列和 AgentLoop 组合为一个唯一后台 worker。"""

    def __init__(
        self,
        opened: OpenSession,
        *,
        loop_factory: Callable[[], object],
        system_prompt: str = IDENTITY,
        max_turns: int = 20,
        todo_store: TodoStore | None = None,
        trace_sink: TraceSink | None = None,
        raise_ui_exceptions: bool = False,
    ) -> None:
        self.session_id = opened.session_id
        self._callbacks: set[UpdateCallback] = set()
        self._engine = SessionEngine(opened, ui_sink=self._publish)
        self._loop_factory = loop_factory
        self._system_prompt = system_prompt
        self._max_turns = max_turns
        self._active_cancellation: Cancellation | None = None
        self._cancellation_requested = False
        self._initial: tuple[UUID, Message] | None = None
        self._stopping = False
        self._worker: asyncio.Task[None] | None = None
        self._todo_store = todo_store
        self._todo_unsubscribe: Callable[[], None] | None = None
        self._trace_sink = trace_sink or NullTraceSink()
        self._raise_ui_exceptions = raise_ui_exceptions
        if todo_store is not None:
            loop = asyncio.get_running_loop()

            def todos_changed(todos) -> None:
                loop.call_soon_threadsafe(asyncio.create_task, self._publish(TodosChanged(todos)))

            self._todo_unsubscribe = todo_store.subscribe(str(self.session_id), todos_changed)

    @classmethod
    async def start(
        cls,
        repository: SessionRepository,
        first_message: Message,
        *,
        loop_factory: Callable[[], object],
        system_prompt: str = IDENTITY,
        max_turns: int = 20,
        todo_store: TodoStore | None = None,
        trace_sink: TraceSink | None = None,
        raise_ui_exceptions: bool = False,
    ) -> tuple[RuntimeSession, AcceptedInput]:
        SessionEngine._validate_user_message(first_message)
        session_id, run_id = uuid4(), uuid4()
        first = JournalRecord(
            1,
            JournalRecordType.USER_MESSAGE,
            session_id,
            run_id,
            datetime.now(timezone.utc),
            UserMessagePayload(first_message),
        )
        opened = await repository.create_session(session_id, first)
        runtime = cls(
            opened,
            loop_factory=loop_factory,
            system_prompt=system_prompt,
            max_turns=max_turns,
            todo_store=todo_store,
            trace_sink=trace_sink,
            raise_ui_exceptions=raise_ui_exceptions,
        )
        runtime._initial = (run_id, first_message)
        runtime.start_worker()
        return runtime, AcceptedInput(first_message.message_id, run_id)

    @classmethod
    async def open(
        cls,
        opened: OpenSession,
        *,
        loop_factory: Callable[[], object],
        system_prompt: str = IDENTITY,
        max_turns: int = 20,
        todo_store: TodoStore | None = None,
        trace_sink: TraceSink | None = None,
        raise_ui_exceptions: bool = False,
    ) -> RuntimeSession:
        runtime = cls(
            opened,
            loop_factory=loop_factory,
            system_prompt=system_prompt,
            max_turns=max_turns,
            todo_store=todo_store,
            trace_sink=trace_sink,
            raise_ui_exceptions=raise_ui_exceptions,
        )
        await runtime._engine.recover_interrupted()
        return runtime

    async def submit(self, message: Message) -> AcceptedInput:
        if self._stopping:
            raise RuntimeError("Session 正在停止")
        queued = await self._engine.submit(message)
        return AcceptedInput(queued.message.message_id, queued.run_id)

    async def withdraw(self, message_id: UUID) -> bool:
        return await self._engine.withdraw(message_id)

    async def snapshot(self) -> SessionSnapshot:
        messages = self._engine.messages
        title = SessionRepository.session_name(next(message for message in messages if message.role is Role.USER))
        todos = self._todo_store.get(str(self.session_id)) if self._todo_store is not None else ()
        return SessionSnapshot(
            messages,
            self._engine.run_outcomes,
            todos,
            self._engine.pending_permission,
            title,
            self._engine.session_usage,
        )

    async def request_permission(self, request: PermissionRequest, cancellation: Cancellation) -> PermissionDecision:
        return await self._engine.request_permission(request, cancellation)

    def resolve_permission(self, decision: PermissionDecision) -> bool:
        return self._engine.resolve_permission(decision)

    def subscribe(self, callback: UpdateCallback) -> Callable[[], None]:
        self._callbacks.add(callback)

        def unsubscribe() -> None:
            self._callbacks.discard(callback)

        return unsubscribe

    @property
    def active(self) -> bool:
        return self._active_cancellation is not None

    @property
    def cancellation_requested(self) -> bool:
        return self._cancellation_requested

    def cancel_active(self) -> bool:
        if self._active_cancellation is None or self._cancellation_requested:
            return False
        self._cancellation_requested = True
        self._active_cancellation.cancel()
        return True

    async def stop(self, reason: str) -> None:
        del reason  # 停止原因属于应用生命周期，不写入 Transcript。
        if self._stopping:
            return
        self._stopping = True
        if self._active_cancellation is not None:
            self._active_cancellation.cancel()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        if self._todo_unsubscribe is not None:
            self._todo_unsubscribe()
            self._todo_unsubscribe = None
        await self._engine.close()

    async def _publish(self, update: object) -> None:
        for callback in tuple(self._callbacks):
            try:
                await callback(update)
            except Exception as exc:
                # UI 通知可丢失；一个订阅者失败不能回滚已持久化事实。
                safe = sanitize_error(exc)
                safe["message"] = ""
                context = TraceContext(uuid4(), uuid4(), None, self.session_id, UUID(int=0))
                try:
                    await self._trace_sink.emit(TraceEvent(
                        TraceEventType.SPAN_FINISHED,
                        context,
                        {"name": "ui_delivery_error", "update_type": type(update).__name__, "error": safe},
                    ))
                except Exception:
                    pass
                if self._raise_ui_exceptions:
                    raise

    def start_worker(self) -> None:
        if self._stopping:
            raise RuntimeError("Session 已停止")
        if self._worker is not None:
            raise RuntimeError("Session worker 已启动")
        self._worker = asyncio.create_task(self._run_worker(), name=f"session-{self.session_id}")

    async def _run_worker(self) -> None:
        if self._initial is not None:
            run_id, message = self._initial
            self._initial = None
            await self._run_committed(run_id, message)
        while True:
            self._active_cancellation = Cancellation()
            self._cancellation_requested = False
            try:
                await self._engine.run_next(
                    self._loop_factory(),
                    self._system_prompt,
                    self._max_turns,
                    self._active_cancellation,
                )
            finally:
                self._active_cancellation = None
                self._cancellation_requested = False

    async def _run_committed(self, run_id: UUID, message: Message) -> None:
        self._active_cancellation = Cancellation()
        self._cancellation_requested = False
        try:
            await self._loop_factory().run(
                self._engine.messages,
                message,
                self._system_prompt,
                self._max_turns,
                self._engine,
                self._active_cancellation,
                run_id,
            )
        finally:
            self._active_cancellation = None
            self._cancellation_requested = False
