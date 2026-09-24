"""ConversationProjection: 将历史快照与实时更新聚合为单一消息树。

该投影是一个同步、确定性的内存模块。``replace`` 从控制器的权威快照进行全量重建；
``apply`` 消费同一会话世代的有序增量更新。两者共享同一条摄取路径，
确保实时消息树能够收敛到与后续快照完全一致的标识、顺序、正文内容及工具归属。

它不读取文件、不写入调用记录、不执行工具，也不判定运行是否已被取消。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from application.history import HistoryRecord
from application.types import (
    AssistantDelta,
    DetailLoaded,
    DetailRef,
    InteractionRequest,
    InteractionRequested,
    InteractionResolved,
    MessageCommitted,
    ModelUsage,
    QueueChanged,
    QueueItem,
    RunCancelled,
    RunCompleted,
    RunFailed,
    RunStarted,
    RunState,
    SessionSnapshot,
    SessionUpdate,
    SnapshotUpdate,
    StatusChanged,
    ToolUpdate,
    UsageChanged,
    UserMessageCommitted,
)
from ui.tui.projection_types import (
    LIFECYCLE_COMPLETED,
    LIFECYCLE_DRAFT,
    MESSAGE_ROLE_ASSISTANT,
    MESSAGE_ROLE_SYSTEM,
    MESSAGE_ROLE_USER,
    PART_ATTACHMENT,
    PART_TEXT,
    PART_TOOL,
    UiMessage,
    UiPart,
    ViewChange,
)

_COMPLETED_TOOL_STATUSES = {"completed", "error"}


@dataclass
class _MessageState:
    """内部可变状态；不可变的冻结 ``UiMessage`` 值均由此派生。"""

    message_id: str
    role: str
    lifecycle: str = LIFECYCLE_DRAFT
    group_key: str | None = None
    text: str = ""
    attachments: list[UiPart] = field(default_factory=list)
    tools: OrderedDict[str, UiPart] = field(default_factory=OrderedDict)


class ConversationProjection:
    def __init__(self, snapshot: SessionSnapshot | None = None) -> None:
        self._session_id = ""
        self._generation = 0
        self._sequence = 0
        self._messages: list[_MessageState] = []
        self._by_id: dict[str, _MessageState] = {}
        self._groups: dict[str, _MessageState] = {}
        self._queue: tuple[QueueItem, ...] = ()
        self._paused = False
        self._status = ""
        self._configured = True
        self._run = RunState()
        self._interaction: InteractionRequest | None = None
        self._pending_interactions: tuple[InteractionRequest, ...] = ()
        self._usage = ModelUsage()
        self._diagnostics: tuple[str, ...] = ()
        self._resync_required = False
        if snapshot is not None:
            self.replace(snapshot)

    # --- 公共读取属性 ---------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def sequence(self) -> int:
        return self._sequence

    @property
    def messages(self) -> tuple[UiMessage, ...]:
        return tuple(self._message_view(state) for state in self._messages)

    @property
    def queue(self) -> tuple[QueueItem, ...]:
        return self._queue

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def status(self) -> str:
        return self._status

    @property
    def configured(self) -> bool:
        return self._configured

    @property
    def run(self) -> RunState:
        return self._run

    @property
    def interaction(self) -> InteractionRequest | None:
        return self._interaction

    @property
    def pending_interactions(self) -> tuple[InteractionRequest, ...]:
        return self._pending_interactions

    @property
    def usage(self) -> ModelUsage:
        return self._usage

    @property
    def diagnostics(self) -> tuple[str, ...]:
        return self._diagnostics

    @property
    def resync_required(self) -> bool:
        return self._resync_required

    # --- 入口方法 ---------------------------------------------------------

    def replace(self, snapshot: SessionSnapshot) -> ViewChange:
        previous = {
            state.message_id: self._message_view(state) for state in self._messages
        }
        previous_order = [state.message_id for state in self._messages]
        reset = (
            self._session_id != snapshot.session_id
            or self._generation != snapshot.generation
        )
        self._messages = []
        self._by_id = {}
        self._groups = {}
        self._session_id = snapshot.session_id
        self._generation = snapshot.generation
        self._sequence = snapshot.sequence
        self._queue = tuple(snapshot.queue)
        self._paused = snapshot.paused
        self._status = snapshot.status
        self._configured = snapshot.configured
        self._run = snapshot.run
        self._interaction = snapshot.interaction
        self._pending_interactions = tuple(snapshot.pending_interactions)
        self._usage = snapshot.usage
        self._diagnostics = tuple(snapshot.diagnostics)
        self._resync_required = False
        for record in snapshot.history:
            self._ingest_history(record)
        self._ingest_active_run(snapshot.run)

        current_order = [state.message_id for state in self._messages]
        updated = tuple(
            state.message_id
            for state in self._messages
            if previous.get(state.message_id) != self._message_view(state)
        )
        deleted = tuple(mid for mid in previous if mid not in self._by_id)
        structure_changed = reset or previous_order != current_order
        return ViewChange(
            updated_ids=updated,
            deleted_ids=deleted,
            structure_changed=structure_changed,
            queue_changed=True,
            status_changed=True,
            interaction_changed=True,
            usage_changed=True,
            run_changed=True,
            reset=reset,
            immediate=True,
        )

    def apply(self, update: SessionUpdate) -> ViewChange:
        if isinstance(update, SnapshotUpdate):
            return self.replace(update.snapshot)
        generation = getattr(update, "generation", None)
        if isinstance(generation, int):
            if self._generation == 0:
                self._generation = generation
            elif generation != self._generation:
                return ViewChange.none()
        sequence = getattr(update, "sequence", None)
        if isinstance(sequence, int):
            if sequence <= self._sequence:
                return ViewChange.none()
            if sequence != self._sequence + 1:
                self._resync_required = True
                return ViewChange(resync_required=True, immediate=True)
            self._sequence = sequence
        return self._dispatch(update)

    # --- 分发处理 ---------------------------------------------------------

    def _dispatch(self, update: SessionUpdate) -> ViewChange:
        if isinstance(update, UserMessageCommitted):
            return self._on_user_committed(update)
        if isinstance(update, RunStarted):
            return self._on_run_started(update)
        if isinstance(update, AssistantDelta):
            return self._on_assistant_delta(update)
        if isinstance(update, MessageCommitted):
            return self._on_message_committed(update)
        if isinstance(update, ToolUpdate):
            return self._on_tool_update(update)
        if isinstance(update, RunCompleted):
            self._run = replace(self._run, active=False, status="completed")
            return ViewChange(run_changed=True, immediate=True)
        if isinstance(update, RunFailed):
            self._run = replace(self._run, active=False, status="failed")
            return ViewChange(run_changed=True, status_changed=True, immediate=True)
        if isinstance(update, RunCancelled):
            self._run = replace(self._run, active=False, status="cancelled")
            return ViewChange(run_changed=True, status_changed=True, immediate=True)
        if isinstance(update, QueueChanged):
            self._queue = tuple(update.queue)
            self._paused = update.paused
            return ViewChange(queue_changed=True)
        if isinstance(update, StatusChanged):
            self._status = update.status
            if update.configured is not None:
                self._configured = update.configured
            return ViewChange(status_changed=True)
        if isinstance(update, InteractionRequested):
            self._interaction = update.request
            pending = [
                request
                for request in self._pending_interactions
                if request.request_id != update.request.request_id
            ]
            pending.append(update.request)
            self._pending_interactions = tuple(pending)
            return ViewChange(interaction_changed=True, immediate=True)
        if isinstance(update, InteractionResolved):
            mutable = [
                request
                for request in self._pending_interactions
                if request.request_id != update.request_id
            ]
            changed = len(mutable) != len(self._pending_interactions)
            if (
                self._interaction is not None
                and self._interaction.request_id == update.request_id
            ):
                self._interaction = None
                changed = True
            self._pending_interactions = tuple(mutable)
            return (
                ViewChange(interaction_changed=True, immediate=True)
                if changed
                else ViewChange.none()
            )
        if isinstance(update, UsageChanged):
            self._usage = update.usage
            return ViewChange(usage_changed=True)
        if isinstance(update, DetailLoaded):
            return self._on_detail_loaded(update)
        return ViewChange.none()

    # --- 消息更新处理 -----------------------------------------------------

    def _on_user_committed(self, update: UserMessageCommitted) -> ViewChange:
        message_id = update.message_uuid or f"input:{update.input_id}"
        removed = self._remove_queued(update.input_id)
        state, created = self._ensure_message(
            message_id, MESSAGE_ROLE_USER, LIFECYCLE_COMPLETED
        )
        state.text = update.text
        return ViewChange(
            updated_ids=(state.message_id,),
            structure_changed=created,
            queue_changed=removed,
        )

    def _on_run_started(self, update: RunStarted) -> ViewChange:
        removed = self._remove_queued(update.input_id)
        self._run = RunState(
            active=True,
            input_id=update.input_id,
            text=update.text,
            status="running",
        )
        return ViewChange(run_changed=True, queue_changed=removed)

    def _on_assistant_delta(self, update: AssistantDelta) -> ViewChange:
        group_key = self._group_key(update.assistant_call_id, update.model_turn_index)
        state, created = self._ensure_assistant(group_key)
        if update.text:
            state.text += update.text
        return ViewChange(updated_ids=(state.message_id,), structure_changed=created)

    def _on_message_committed(self, update: MessageCommitted) -> ViewChange:
        group_key = self._group_key(update.assistant_call_id, update.model_turn_index)
        state = self._groups.get(group_key)
        created = False
        if state is None:
            state, created = self._ensure_assistant(
                group_key,
                message_id=update.message_uuid or None,
                lifecycle=LIFECYCLE_COMPLETED,
            )
        else:
            state.lifecycle = LIFECYCLE_COMPLETED
            if update.message_uuid and state.message_id != update.message_uuid:
                self._relink(state, update.message_uuid)
        # 最终提交会替换消息正文，绝不在增量 delta 之上重复追加。
        if update.text:
            state.text = update.text
        return ViewChange(
            updated_ids=(state.message_id,),
            structure_changed=created,
            immediate=True,
        )

    def _on_tool_update(self, update: ToolUpdate) -> ViewChange:
        if update.assistant_call_id is None and update.model_turn_index is None:
            # 缺少明确的归属信息时，无法为工具调用指定稳定的宿主消息；
            # 切勿凭空捏造“最近的 assistant”或创建永久的孤儿消息。
            existing = self._find_unique_tool(update.tool_call_id)
            if existing is None or existing.group_key is None:
                return ViewChange.none()
            group_key = existing.group_key
        else:
            group_key = self._group_key(
                update.assistant_call_id, update.model_turn_index
            )
        metadata: Mapping[str, Any] = (
            update.result.metadata if update.result is not None else {}
        )
        state, created = self._apply_tool(
            group_key,
            tool_call_id=update.tool_call_id,
            tool_name=update.tool_name,
            status=update.status,
            text=update.text,
            is_error=update.is_error,
            tool_input=update.input,
            metadata=metadata,
        )
        return ViewChange(updated_ids=(state.message_id,), structure_changed=created)

    def _on_detail_loaded(self, update: DetailLoaded) -> ViewChange:
        if update.ref.session_id != self._session_id:
            return ViewChange.none()
        matched: list[str] = []
        for state in self._messages:
            for tool_id, part in list(state.tools.items()):
                if part.detail_ref is None or part.detail_ref != update.ref:
                    continue
                updated = replace(part, detail_loaded=True)
                if update.result.success:
                    updated = replace(
                        updated,
                        result_preview=update.result.text,
                        detail_error=None,
                    )
                else:
                    updated = replace(
                        updated,
                        detail_error=(
                            "missing_artifact"
                            if update.result.missing
                            else update.result.error or "detail_failed"
                        ),
                    )
                state.tools[tool_id] = updated
                matched.append(state.message_id)
        return ViewChange(updated_ids=tuple(dict.fromkeys(matched)))

    # --- 历史记录摄取 -----------------------------------------------------

    def _ingest_history(self, record: HistoryRecord) -> None:
        role = record.role
        if role == "user":
            state, _ = self._ensure_message(
                record.uuid, MESSAGE_ROLE_USER, LIFECYCLE_COMPLETED
            )
            state.text = record.text
            state.attachments = [
                UiPart(
                    kind=PART_ATTACHMENT,
                    content=attachment.summary,
                    attachment_type=attachment.attachment_type,
                )
                for attachment in record.attachments
            ]
            return
        if role == "assistant":
            group_key = self._group_key(
                record.assistant_call_id, record.model_turn_index, record.uuid
            )
            state, _ = self._ensure_assistant(
                group_key, message_id=record.uuid, lifecycle=LIFECYCLE_COMPLETED
            )
            state.text = record.text
            for call in record.tool_calls:
                state.tools.setdefault(
                    call.tool_call_id,
                    UiPart(
                        kind=PART_TOOL,
                        tool_call_id=call.tool_call_id,
                        tool_name=call.tool_name,
                        tool_input=dict(call.input),
                        status="declared",
                    ),
                )
            return
        if role == "tool_result":
            self._merge_history_tool_result(record)

    def _merge_history_tool_result(self, record: HistoryRecord) -> None:
        tool_call_id = record.tool_call_id
        if tool_call_id is None:
            return
        state = self._group_for_record(record)
        part = state.tools.get(tool_call_id) if state is not None else None
        if part is None:
            state = self._find_declared_tool(tool_call_id)
            part = state.tools.get(tool_call_id) if state is not None else None
        if state is None or part is None:
            # 无法配对的工具结果绝不会演化为永久的对话消息。
            return
        detail_ref = None
        if record.externalized and record.external_result_path:
            detail_ref = DetailRef(
                session_id=self._session_id,
                kind="tool_result",
                identifier=tool_call_id,
                relative_path=record.external_result_path,
            )
        detail_error = "missing_artifact" if record.missing_external_result else None
        state.tools[tool_call_id] = replace(
            part,
            status="error" if record.is_error else "completed",
            is_error=record.is_error,
            result_preview=record.text,
            detail_ref=detail_ref,
            detail_error=detail_error,
        )

    def _ingest_active_run(self, run: RunState) -> None:
        if not run.active or not (run.assistant_text or run.tools):
            return
        group_key = self._group_key(
            run.assistant_call_id, run.model_turn_index, run.run_id
        )
        state, _ = self._ensure_assistant(group_key)
        if run.assistant_text:
            state.text = run.assistant_text
        for tool in run.tools:
            self._apply_tool(
                group_key,
                tool_call_id=tool.tool_call_id,
                tool_name=tool.tool_name,
                status=tool.status,
                text=tool.text,
                is_error=tool.is_error,
                tool_input=tool.input,
                metadata=tool.metadata,
            )

    # --- 共享工具应用处理 -------------------------------------------------

    def _apply_tool(
        self,
        group_key: str,
        *,
        tool_call_id: str,
        tool_name: str,
        status: str,
        text: str,
        is_error: bool,
        tool_input: Mapping[str, Any],
        metadata: Mapping[str, Any],
    ) -> tuple[_MessageState, bool]:
        state, created = self._ensure_assistant(group_key)
        part = state.tools.get(tool_call_id)
        resolved_input: Mapping[str, Any] = {}
        if tool_input:
            resolved_input = dict(tool_input)
        elif part is not None:
            resolved_input = part.tool_input
        fields: dict[str, Any] = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name
            or (part.tool_name if part is not None else "unknown_tool"),
            "status": status,
            "is_error": is_error,
            "tool_input": resolved_input,
        }
        if part is None:
            part = UiPart(kind=PART_TOOL, **fields)
        else:
            part = replace(part, **fields)
        if status in _COMPLETED_TOOL_STATUSES:
            detail_ref = part.detail_ref
            candidate = self._detail_ref_from_metadata(tool_call_id, metadata)
            if candidate is not None:
                detail_ref = candidate
            part = replace(part, result_preview=text, detail_ref=detail_ref)
        elif status == "progress":
            part = replace(part, progress_text=text)
        state.tools[tool_call_id] = part
        return state, created

    def _detail_ref_from_metadata(
        self, tool_call_id: str, metadata: Mapping[str, Any]
    ) -> DetailRef | None:
        if metadata.get("tool_result_externalized") is not True:
            return None
        relative_path = metadata.get("tool_result_path")
        if not isinstance(relative_path, str) or not relative_path:
            return None
        return DetailRef(
            session_id=self._session_id,
            kind="tool_result",
            identifier=tool_call_id,
            relative_path=relative_path,
        )

    # --- 状态辅助方法 -----------------------------------------------------

    def _ensure_message(
        self, message_id: str, role: str, lifecycle: str
    ) -> tuple[_MessageState, bool]:
        state = self._by_id.get(message_id)
        if state is not None:
            return state, False
        state = _MessageState(message_id=message_id, role=role, lifecycle=lifecycle)
        self._messages.append(state)
        self._by_id[message_id] = state
        return state, True

    def _ensure_assistant(
        self,
        group_key: str,
        *,
        message_id: str | None = None,
        lifecycle: str = LIFECYCLE_DRAFT,
    ) -> tuple[_MessageState, bool]:
        state = self._groups.get(group_key)
        if state is not None:
            return state, False
        state = _MessageState(
            message_id=message_id or self._synthetic_id(group_key),
            role=MESSAGE_ROLE_ASSISTANT,
            lifecycle=lifecycle,
            group_key=group_key,
        )
        self._messages.append(state)
        self._by_id[state.message_id] = state
        self._groups[group_key] = state
        return state, True

    def _relink(self, state: _MessageState, new_id: str) -> None:
        if state.message_id == new_id:
            return
        self._by_id.pop(state.message_id, None)
        state.message_id = new_id
        self._by_id[new_id] = state

    def _remove_queued(self, input_id: str) -> bool:
        if any(item.input_id == input_id for item in self._queue):
            self._queue = tuple(
                item for item in self._queue if item.input_id != input_id
            )
            return True
        return False

    def _group_for_record(self, record: HistoryRecord) -> _MessageState | None:
        group_key = self._group_key_or_none(
            record.assistant_call_id, record.model_turn_index
        )
        if group_key is None:
            return None
        return self._groups.get(group_key)

    def _find_declared_tool(self, tool_call_id: str) -> _MessageState | None:
        matches = [
            state
            for state in self._groups.values()
            if (part := state.tools.get(tool_call_id)) is not None
            and part.result_preview is None
        ]
        return matches[0] if len(matches) == 1 else None

    def _find_unique_tool(self, tool_call_id: str) -> _MessageState | None:
        matches = [
            state for state in self._groups.values() if tool_call_id in state.tools
        ]
        return matches[0] if len(matches) == 1 else None

    def _group_key(
        self,
        assistant_call_id: str | None,
        model_turn_index: int | None,
        fallback: str | None = None,
    ) -> str:
        key = self._group_key_or_none(assistant_call_id, model_turn_index)
        if key is not None:
            return key
        return f"{self._session_id}|record|{fallback}"

    def _group_key_or_none(
        self, assistant_call_id: str | None, model_turn_index: int | None
    ) -> str | None:
        if assistant_call_id is None and model_turn_index is None:
            return None
        return f"{self._session_id}|call|{model_turn_index}|{assistant_call_id}"

    def _synthetic_id(self, group_key: str) -> str:
        return f"draft::{group_key}"

    def _message_view(self, state: _MessageState) -> UiMessage:
        parts: list[UiPart] = []
        if (
            state.role
            in {
                MESSAGE_ROLE_USER,
                MESSAGE_ROLE_ASSISTANT,
                MESSAGE_ROLE_SYSTEM,
            }
            and state.text
        ):
            parts.append(UiPart(kind=PART_TEXT, content=state.text))
        if state.role == MESSAGE_ROLE_USER:
            parts.extend(state.attachments)
        elif state.role == MESSAGE_ROLE_ASSISTANT:
            parts.extend(state.tools.values())
        return UiMessage(
            message_id=state.message_id,
            role=state.role,
            parts=tuple(parts),
            lifecycle=state.lifecycle,
        )


__all__ = ["ConversationProjection"]
