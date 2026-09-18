"""协调权限、提问、信任与计划审批请求。

同一时刻只有一个请求处于活动状态；后续请求按到达顺序排队等待。
协调器本身从不决定策略：守卫的 deny 从不会传递到此处；
回答通过请求 ID 和请求类型进行校验，因此过期或不匹配的回答无法满足其他请求。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from application.types import (
    InteractionAnswer,
    InteractionKind,
    InteractionRequest,
    ResponseResult,
)
from services.permissions.types import PermissionRequest, PermissionResponse
from services.questions.types import QuestionRequest, QuestionResponse


@dataclass
class _Pending:
    request: InteractionRequest
    future: asyncio.Future[InteractionAnswer] = field(repr=False)
    resolved: bool = False


class InteractionCoordinator:
    def __init__(
        self,
        *,
        session_id: str,
        on_request: Callable[[InteractionRequest], None] | None = None,
        on_resolved: Callable[[str, str], None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._on_request = on_request
        self._on_resolved = on_resolved
        self._pending: list[_Pending] = []
        self._active: _Pending | None = None
        self._closed = False

    # --- public API -------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    def active_request(self) -> InteractionRequest | None:
        return self._active.request if self._active is not None else None

    def pending_requests(self) -> tuple[InteractionRequest, ...]:
        return tuple(item.request for item in self._pending)

    async def request(
        self,
        kind: InteractionKind,
        payload: Any,
        *,
        run_id: str | None = None,
        options: tuple[Any, ...] = (),
    ) -> InteractionAnswer:
        if self._closed:
            return InteractionAnswer(
                request_id="", kind=kind, cancelled=True, payload=None
            )
        request = InteractionRequest(
            request_id=f"{kind}-{uuid4().hex[:12]}",
            session_id=self._session_id,
            kind=kind,
            payload=payload,
            run_id=run_id,
            options=options,
        )
        future: asyncio.Future[InteractionAnswer] = (
            asyncio.get_running_loop().create_future()
        )
        pending = _Pending(request=request, future=future)
        self._pending.append(pending)
        activated = self._active is None
        if activated:
            self._active = pending
            self._notify_request(request)
        try:
            return await future
        except asyncio.CancelledError:
            self._discard(pending, outcome="cancelled")
            raise

    async def resolve(
        self,
        request_id: str,
        kind: InteractionKind,
        payload: Any = None,
    ) -> ResponseResult:
        pending = self._find(request_id)
        if pending is None:
            return ResponseResult(
                request_id=request_id, accepted=False, reason="expired_or_unknown"
            )
        if pending.request.kind != kind:
            return ResponseResult(
                request_id=request_id, accepted=False, reason="request_type_mismatch"
            )
        self._finish(
            pending,
            InteractionAnswer(
                request_id=request_id, kind=kind, payload=payload, cancelled=False
            ),
        )
        return ResponseResult(request_id=request_id, accepted=True)

    async def cancel_all(self, outcome: str = "cancelled") -> None:
        self.cancel_pending(outcome)

    def cancel_pending(self, outcome: str = "cancelled") -> None:
        """同步唤醒所有等待者。可安全地从取消路径调用。"""

        pending_items = tuple(self._pending)
        self._pending.clear()
        self._active = None
        for pending in pending_items:
            pending.resolved = True
            if not pending.future.done():
                pending.future.set_result(
                    InteractionAnswer(
                        request_id=pending.request.request_id,
                        kind=pending.request.kind,
                        payload=None,
                        cancelled=True,
                    )
                )
            self._notify_resolved(pending.request.request_id, outcome)

    def close(self) -> None:
        """同步标记为已关闭并唤醒等待者，不发送通知。"""

        self._closed = True
        for pending in tuple(self._pending):
            if not pending.future.done():
                pending.future.set_result(
                    InteractionAnswer(
                        request_id=pending.request.request_id,
                        kind=pending.request.kind,
                        payload=None,
                        cancelled=True,
                    )
                )
        self._pending.clear()
        self._active = None

    # --- internals --------------------------------------------------------

    def _find(self, request_id: str) -> _Pending | None:
        for pending in self._pending:
            if pending.request.request_id == request_id:
                return pending
        return None

    def _discard(self, pending: _Pending, *, outcome: str) -> None:
        if pending.resolved:
            return
        pending.resolved = True
        if pending in self._pending:
            self._pending.remove(pending)
        was_active = self._active is pending
        if was_active:
            self._active = None
            self._activate_next()
        self._notify_resolved(pending.request.request_id, outcome)

    def _finish(
        self,
        pending: _Pending,
        answer: InteractionAnswer,
        *,
        outcome: str = "answered",
        notify: bool = True,
    ) -> None:
        if pending.resolved:
            return
        pending.resolved = True
        if pending in self._pending:
            self._pending.remove(pending)
        if not pending.future.done():
            pending.future.set_result(answer)
        if self._active is pending:
            self._active = None
            self._activate_next()
        if notify:
            self._notify_resolved(pending.request.request_id, outcome)

    def _activate_next(self) -> None:
        if self._active is not None or not self._pending:
            return
        self._active = self._pending[0]
        self._notify_request(self._active.request)

    def _notify_request(self, request: InteractionRequest) -> None:
        if self._on_request is not None:
            self._on_request(request)

    def _notify_resolved(self, request_id: str, outcome: str) -> None:
        if self._on_resolved is not None:
            self._on_resolved(request_id, outcome)


class PermissionPromptAdapter:
    """将交互协调器适配为 PermissionPrompter。"""

    def __init__(self, coordinator: InteractionCoordinator) -> None:
        self._coordinator = coordinator

    async def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        answer = await self._coordinator.request(
            "permission",
            payload=request,
            run_id=getattr(request.tool_call, "id", None),
            options=tuple(request.options),
        )
        if answer.cancelled:
            return PermissionResponse(
                action="deny",
                feedback="Permission request was cancelled.",
            )
        if isinstance(answer.payload, PermissionResponse):
            return answer.payload
        return PermissionResponse(
            action="deny",
            feedback="Permission request received an invalid answer.",
        )

    def prompt_options(self, request: PermissionRequest) -> tuple[Any, ...]:
        return tuple(request.options)


class UserQuestionPromptAdapter:
    """将交互协调器适配为 UserQuestionPrompter。"""

    def __init__(self, coordinator: InteractionCoordinator) -> None:
        self._coordinator = coordinator

    async def ask_questions(
        self,
        questions: tuple[QuestionRequest, ...],
    ) -> QuestionResponse:
        answer = await self._coordinator.request(
            "question",
            payload=questions,
            options=tuple(questions),
        )
        if answer.cancelled:
            return QuestionResponse(declined=True, feedback="cancelled")
        if isinstance(answer.payload, QuestionResponse):
            return answer.payload
        return QuestionResponse(declined=True, feedback="invalid_answer")


__all__ = [
    "InteractionCoordinator",
    "PermissionPromptAdapter",
    "UserQuestionPromptAdapter",
]
