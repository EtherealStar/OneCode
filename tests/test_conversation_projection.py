from __future__ import annotations

from typing import Any

from application.history import (
    HistoryAttachmentSummary,
    HistoryRecord,
    HistoryToolCall,
)
from application.types import (
    AssistantDelta,
    DetailLoaded,
    DetailRef,
    DetailResult,
    InteractionRequest,
    InteractionRequested,
    InteractionResolved,
    MessageCommitted,
    QueueChanged,
    QueueItem,
    RunCancelled,
    RunState,
    SessionSnapshot,
    StatusChanged,
    ToolUpdate,
    UsageChanged,
    UserMessageCommitted,
)
from services.model.types import ModelUsage
from services.tools.types import ToolExecutionResult
from ui.tui.projection import ConversationProjection
from ui.tui.projection_types import ViewChange


def snapshot(
    *,
    session_id: str = "s1",
    generation: int = 1,
    sequence: int = 0,
    history: tuple[HistoryRecord, ...] = (),
    run: RunState | None = None,
    queue: tuple[QueueItem, ...] = (),
    status: str = "ready",
    interaction: InteractionRequest | None = None,
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        generation=generation,
        sequence=sequence,
        initialized=True,
        configured=True,
        paused=False,
        status=status,
        run=run or RunState(),
        queue=queue,
        history=history,
        interaction=interaction,
    )


def user_record(uuid: str, text: str, *, attachments=()) -> HistoryRecord:
    return HistoryRecord(
        uuid=uuid, parent_uuid=None, role="user", text=text, attachments=attachments
    )


def assistant_record(
    uuid: str,
    text: str,
    *,
    assistant_call_id: str | None = None,
    model_turn_index: int | None = None,
    tool_calls: tuple[HistoryToolCall, ...] = (),
) -> HistoryRecord:
    return HistoryRecord(
        uuid=uuid,
        parent_uuid=None,
        role="assistant",
        text=text,
        assistant_call_id=assistant_call_id,
        model_turn_index=model_turn_index,
        tool_calls=tool_calls,
    )


def tool_record(
    uuid: str,
    tool_call_id: str,
    text: str,
    *,
    assistant_call_id: str | None = None,
    model_turn_index: int | None = None,
    is_error: bool = False,
    externalized: bool = False,
    path: str | None = None,
    missing: bool = False,
) -> HistoryRecord:
    return HistoryRecord(
        uuid=uuid,
        parent_uuid=None,
        role="tool_result",
        text=text,
        tool_call_id=tool_call_id,
        tool_name="read_file",
        is_error=is_error,
        assistant_call_id=assistant_call_id,
        model_turn_index=model_turn_index,
        externalized=externalized,
        external_result_path=path,
        missing_external_result=missing,
    )


def tree(projection: ConversationProjection) -> list[tuple[Any, ...]]:
    return [
        (
            message.message_id,
            message.role,
            tuple(
                (
                    part.kind,
                    part.content,
                    part.tool_call_id,
                    part.tool_name,
                    tuple(sorted(part.tool_input.items())),
                    part.status,
                    part.result_preview,
                    part.is_error,
                )
                for part in message.parts
            ),
        )
        for message in projection.messages
    ]


def live_projection(updates: list[Any], *, session_id: str = "s1") -> ConversationProjection:
    projection = ConversationProjection(snapshot(session_id=session_id))
    for sequence, update in enumerate(updates, start=1):
        change = projection.apply(_with_sequence(update, sequence))
        assert not change.resync_required
    return projection


def _with_sequence(update: Any, sequence: int) -> Any:
    from dataclasses import replace

    return replace(update, sequence=sequence)


# --- M3.1: identity, order, and tool attribution ---------------------------


def test_live_and_history_produce_same_message_tree() -> None:
    live = live_projection(
        [
            UserMessageCommitted(
                generation=1,
                sequence=0,
                input_id="in-1",
                message_uuid="u1",
                text="hello",
            ),
            AssistantDelta(
                generation=1,
                sequence=0,
                text="he",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            AssistantDelta(
                generation=1,
                sequence=0,
                text="llo",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="hello",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )
    history = ConversationProjection(
        snapshot(
            history=(
                user_record("u1", "hello"),
                assistant_record(
                    "a1", "hello", assistant_call_id="c1", model_turn_index=1
                ),
            )
        )
    )

    assert tree(live) == tree(history)


def test_tools_follow_declaration_order_and_late_result_is_visible() -> None:
    view = live_projection(
        [
            UserMessageCommitted(
                generation=1,
                sequence=0,
                input_id="in-1",
                message_uuid="u1",
                text="run",
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="read_file",
                status="declared",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="B",
                tool_name="bash",
                status="declared",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="B",
                tool_name="bash",
                status="completed",
                text="b done",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )

    assistant = view.messages[-1]
    tool_parts = [part for part in assistant.parts if part.kind == "tool"]
    assert [part.tool_call_id for part in tool_parts] == ["A", "B"]
    assert tool_parts[0].status == "declared"
    assert tool_parts[1].status == "completed"
    assert tool_parts[1].result_preview == "b done"

    view.apply(
        ToolUpdate(
            generation=1,
            sequence=5,
            tool_call_id="A",
            tool_name="read_file",
            status="completed",
            text="a done",
            assistant_call_id="c1",
            model_turn_index=1,
        )
    )
    tool_parts = [part for part in view.messages[-1].parts if part.kind == "tool"]
    assert [part.result_preview for part in tool_parts] == ["a done", "b done"]


def test_duplicate_tool_ids_do_not_cross_model_calls() -> None:
    view = live_projection(
        [
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="",
                assistant_call_id="dup",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="t",
                tool_name="read_file",
                status="declared",
                assistant_call_id="dup",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="t",
                tool_name="read_file",
                status="completed",
                text="first",
                assistant_call_id="dup",
                model_turn_index=1,
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a2",
                text="",
                assistant_call_id="dup",
                model_turn_index=2,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="t",
                tool_name="read_file",
                status="declared",
                assistant_call_id="dup",
                model_turn_index=2,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="t",
                tool_name="read_file",
                status="completed",
                text="second",
                assistant_call_id="dup",
                model_turn_index=2,
            ),
        ]
    )

    assistants = [message for message in view.messages if message.role == "assistant"]
    assert [message.message_id for message in assistants] == ["a1", "a2"]
    first_results = [p.result_preview for p in assistants[0].parts if p.kind == "tool"]
    second_results = [p.result_preview for p in assistants[1].parts if p.kind == "tool"]
    assert first_results == ["first"]
    assert second_results == ["second"]


def test_duplicate_finalization_does_not_repeat_body() -> None:
    view = live_projection(
        [
            AssistantDelta(
                generation=1,
                sequence=0,
                text="hello",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="hello",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="hello",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )

    assistants = [message for message in view.messages if message.role == "assistant"]
    assert len(assistants) == 1
    text_parts = [part.content for part in assistants[0].parts if part.kind == "text"]
    assert text_parts == ["hello"]


def test_late_finalization_replaces_body_and_keeps_results() -> None:
    view = live_projection(
        [
            AssistantDelta(
                generation=1,
                sequence=0,
                text="hel",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="read_file",
                status="declared",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="read_file",
                status="completed",
                text="result",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="hello",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )

    assistant = view.messages[-1]
    assert assistant.message_id == "a1"
    assert [part.content for part in assistant.parts if part.kind == "text"] == ["hello"]
    tools = [part for part in assistant.parts if part.kind == "tool"]
    assert tools[0].result_preview == "result"
    assert tools[0].status == "completed"


def test_tools_only_assistant_container_is_created_and_linked() -> None:
    view = live_projection(
        [
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="bash",
                status="declared",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )

    assistants = [message for message in view.messages if message.role == "assistant"]
    assert len(assistants) == 1
    assert assistants[0].message_id == "a1"
    assert [part.kind for part in assistants[0].parts] == ["tool"]


def test_withdraw_and_commit_keep_queue_projection_separate() -> None:
    projection = ConversationProjection(snapshot())
    projection.apply(
        QueueChanged(
            generation=1,
            sequence=1,
            queue=(
                QueueItem(input_id="i1", text="first"),
                QueueItem(input_id="i2", text="second"),
            ),
            paused=False,
        )
    )
    assert [item.input_id for item in projection.queue] == ["i1", "i2"]
    # No queued input is a committed chat message yet.
    assert projection.messages == ()

    projection.apply(
        UserMessageCommitted(
            generation=1,
            sequence=2,
            input_id="i1",
            message_uuid="u1",
            text="first",
        )
    )
    assert [item.input_id for item in projection.queue] == ["i2"]
    assert [message.message_id for message in projection.messages] == ["u1"]

    projection.apply(
        QueueChanged(generation=1, sequence=3, queue=(QueueItem(input_id="i2", text="second"),), paused=False)
    )
    projection.apply(
        QueueChanged(generation=1, sequence=4, queue=(), paused=False)
    )
    assert projection.queue == ()


def test_attachment_summary_comes_from_structured_metadata() -> None:
    history = ConversationProjection(
        snapshot(
            history=(
                HistoryRecord(
                    uuid="u1",
                    parent_uuid=None,
                    role="user",
                    text="see @note.txt",
                    attachments=(
                        HistoryAttachmentSummary(
                            attachment_id="att-1",
                            attachment_type="file",
                            source="inline",
                            summary="[attachment file] note.txt",
                        ),
                    ),
                ),
            )
        )
    )

    user = history.messages[0]
    attachment_parts = [part for part in user.parts if part.kind == "attachment"]
    assert len(attachment_parts) == 1
    assert attachment_parts[0].content == "[attachment file] note.txt"
    assert attachment_parts[0].attachment_type == "file"


def test_real_error_state_comes_from_result_fields() -> None:
    view = live_projection(
        [
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="read_file",
                status="error",
                text='{"error": "boom"}',
                is_error=True,
                assistant_call_id="c1",
                model_turn_index=1,
                result=ToolExecutionResult(
                    tool_call_id="A",
                    tool_name="read_file",
                    content='{"error": "boom"}',
                    is_error=True,
                ),
            )
        ]
    )

    tool = next(part for part in view.messages[-1].parts if part.kind == "tool")
    assert tool.is_error is True
    assert tool.status == "error"


# --- M3.2: corrections, resync, details, auxiliary state -------------------


def test_duplicate_and_old_generation_updates_are_rejected() -> None:
    projection = ConversationProjection(snapshot())
    first = AssistantDelta(
        generation=1,
        sequence=1,
        text="a",
        assistant_call_id="c1",
        model_turn_index=1,
    )
    assert projection.apply(first).updated_ids
    duplicate = projection.apply(first)
    assert duplicate.empty
    assert len(projection.messages) == 1

    old_generation = AssistantDelta(
        generation=2,
        sequence=2,
        text="b",
        assistant_call_id="c2",
        model_turn_index=1,
    )
    assert projection.apply(old_generation).empty
    assert len(projection.messages) == 1


def test_sequence_gap_requests_a_full_snapshot() -> None:
    projection = ConversationProjection(snapshot(sequence=0))
    change = projection.apply(
        AssistantDelta(
            generation=1,
            sequence=5,
            text="x",
            assistant_call_id="c1",
            model_turn_index=1,
        )
    )
    assert isinstance(change, ViewChange)
    assert change.resync_required is True
    assert projection.resync_required is True
    assert projection.messages == ()

    projection.replace(snapshot(sequence=9))
    assert projection.resync_required is False


def test_detail_load_updates_tool_part_in_place() -> None:
    view = live_projection(
        [
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="grep",
                status="completed",
                text="preview",
                assistant_call_id="c1",
                model_turn_index=1,
                result=ToolExecutionResult(
                    tool_call_id="A",
                    tool_name="grep",
                    content="preview",
                    metadata={
                        "tool_result_externalized": True,
                        "tool_result_path": "tool-results/A.txt",
                    },
                ),
            )
        ]
    )
    tool = next(part for part in view.messages[-1].parts if part.kind == "tool")
    assert tool.detail_ref is not None

    change = view.apply(
        DetailLoaded(
            generation=1,
            sequence=2,
            ref=tool.detail_ref,
            result=DetailResult(success=True, text="full body", externalized=True),
        )
    )
    assert change.updated_ids
    updated = next(part for part in view.messages[-1].parts if part.kind == "tool")
    assert updated.result_preview == "full body"
    assert updated.detail_loaded is True

    # A late result for another session never reaches this projection.
    stale = DetailRef(
        session_id="other",
        kind="tool_result",
        identifier="A",
        relative_path="tool-results/A.txt",
    )
    assert view.apply(
        DetailLoaded(
            generation=1,
            sequence=3,
            ref=stale,
            result=DetailResult(success=True, text="other"),
        )
    ).empty


def test_missing_detail_keeps_summary_and_marks_state() -> None:
    view = live_projection(
        [
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="A",
                tool_name="grep",
                status="completed",
                text="preview",
                assistant_call_id="c1",
                model_turn_index=1,
                result=ToolExecutionResult(
                    tool_call_id="A",
                    tool_name="grep",
                    content="preview",
                    metadata={
                        "tool_result_externalized": True,
                        "tool_result_path": "tool-results/A.txt",
                    },
                ),
            )
        ]
    )
    tool = next(part for part in view.messages[-1].parts if part.kind == "tool")
    view.apply(
        DetailLoaded(
            generation=1,
            sequence=2,
            ref=tool.detail_ref,
            result=DetailResult(success=False, missing=True, error="missing_artifact"),
        )
    )
    updated = next(part for part in view.messages[-1].parts if part.kind == "tool")
    assert updated.detail_error == "missing_artifact"
    assert updated.result_preview == "preview"


def test_status_and_interaction_changes_notify_without_message_changes() -> None:
    projection = ConversationProjection(snapshot())
    status_change = projection.apply(
        StatusChanged(generation=1, sequence=1, status="running", configured=True)
    )
    assert status_change.status_changed is True
    assert status_change.updated_ids == ()

    request = InteractionRequest(
        request_id="r1", session_id="s1", kind="permission", payload={"x": 1}
    )
    requested = projection.apply(
        InteractionRequested(generation=1, sequence=2, request=request)
    )
    assert requested.interaction_changed is True
    assert projection.interaction == request

    resolved = projection.apply(
        InteractionResolved(
            generation=1, sequence=3, request_id="r1", outcome="allowed"
        )
    )
    assert resolved.interaction_changed is True
    assert projection.interaction is None


def test_snapshot_replacement_preserves_same_session_message_ids() -> None:
    view = live_projection(
        [
            UserMessageCommitted(
                generation=1,
                sequence=0,
                input_id="in-1",
                message_uuid="u1",
                text="hello",
            ),
            MessageCommitted(
                generation=1,
                sequence=0,
                message_uuid="a1",
                text="hi",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )
    before = tree(view)

    change = view.replace(
        snapshot(
            generation=1,
            sequence=50,
            history=(
                user_record("u1", "hello"),
                assistant_record(
                    "a1", "hi", assistant_call_id="c1", model_turn_index=1
                ),
            ),
        )
    )
    assert tree(view) == before
    # A same-session authoritative correction keeps identity and does not
    # signal a session reset.
    assert change.reset is False
    assert change.deleted_ids == ()


def test_snapshot_replacement_reports_deleted_ids() -> None:
    view = ConversationProjection(
        snapshot(history=(user_record("u1", "one"), user_record("u2", "two")))
    )
    change = view.replace(snapshot(history=(user_record("u1", "one"),)))
    assert "u2" in change.deleted_ids
    assert [message.message_id for message in view.messages] == ["u1"]


def test_interrupt_correction_removes_unpaired_tools_without_notice() -> None:
    view = live_projection(
        [
            UserMessageCommitted(
                generation=1,
                sequence=0,
                input_id="in-1",
                message_uuid="u1",
                text="go",
            ),
            AssistantDelta(
                generation=1,
                sequence=0,
                text="half",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
            ToolUpdate(
                generation=1,
                sequence=0,
                tool_call_id="unpaired",
                tool_name="bash",
                status="declared",
                assistant_call_id="c1",
                model_turn_index=1,
            ),
        ]
    )
    assert any(
        part.kind == "tool" for part in view.messages[-1].parts
    )

    # The authoritative cleanup kept the half text and dropped the unpaired call.
    view.replace(
        snapshot(
            generation=1,
            sequence=60,
            history=(
                user_record("u1", "go"),
                assistant_record(
                    "a1", "half", assistant_call_id="c1", model_turn_index=1
                ),
            ),
        )
    )
    assistant = view.messages[-1]
    assert [part.kind for part in assistant.parts] == ["text"]
    assert assistant.parts[0].content == "half"


def test_history_tool_result_merges_into_declaration() -> None:
    view = ConversationProjection(
        snapshot(
            history=(
                assistant_record(
                    "a1",
                    "",
                    assistant_call_id="c1",
                    model_turn_index=1,
                    tool_calls=(HistoryToolCall(tool_call_id="A", tool_name="read_file"),),
                ),
                tool_record(
                    "t1",
                    "A",
                    "content",
                    assistant_call_id="c1",
                    model_turn_index=1,
                ),
            )
        )
    )
    assistant = view.messages[0]
    tools = [part for part in assistant.parts if part.kind == "tool"]
    assert len(tools) == 1
    assert tools[0].result_preview == "content"


def test_usage_update_is_visible_without_message_changes() -> None:
    projection = ConversationProjection(snapshot())
    change = projection.apply(
        UsageChanged(
            generation=1,
            sequence=1,
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )
    )
    assert change.usage_changed is True
    assert change.updated_ids == ()
    assert projection.usage.input_tokens == 10
    assert projection.usage.output_tokens == 5


def test_pending_interactions_are_tracked_and_removed() -> None:
    projection = ConversationProjection(snapshot())
    first = InteractionRequest(
        request_id="r1", session_id="s1", kind="permission", payload={}
    )
    second = InteractionRequest(
        request_id="r2", session_id="s1", kind="question", payload={}
    )
    projection.apply(
        InteractionRequested(generation=1, sequence=1, request=first)
    )
    projection.apply(
        InteractionRequested(generation=1, sequence=2, request=second)
    )
    assert [r.request_id for r in projection.pending_interactions] == ["r1", "r2"]

    stale = projection.apply(
        InteractionResolved(
            generation=1, sequence=3, request_id="missing", outcome="ignored"
        )
    )
    assert stale.empty
    assert [r.request_id for r in projection.pending_interactions] == ["r1", "r2"]

    projection.apply(
        InteractionResolved(
            generation=1, sequence=4, request_id="r1", outcome="allowed"
        )
    )
    assert [r.request_id for r in projection.pending_interactions] == ["r2"]


def test_unattributed_tool_result_does_not_create_permanent_orphan() -> None:
    projection = ConversationProjection(snapshot())
    change = projection.apply(
        ToolUpdate(
            generation=1,
            sequence=1,
            tool_call_id="A",
            tool_name="read_file",
            status="completed",
            text="orphan",
        )
    )
    assert change.empty
    assert projection.messages == ()


def test_cancel_update_marks_run_inactive() -> None:
    projection = ConversationProjection(
        snapshot(run=RunState(active=True, input_id="i1", text="go", status="running"))
    )
    change = projection.apply(
        RunCancelled(generation=1, sequence=1, input_id="i1")
    )
    assert change.run_changed is True
    assert projection.run.active is False
