"""User-visible conversation history derived from the session transcript.

The model active chain and the chat history are read differently. The active
chain is what the next model call receives; chat history keeps pre-compaction
messages, filters compaction copies and internal attachment roles, and never
restores external tool-result bodies. Each history record keeps the stable
transcript identity it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.context.message_shapes import assistant_tool_call_ids
from services.context.recovery import select_active_chain
from services.context.transcript import (
    InMemoryTranscriptStore,
    JsonlTranscriptStore,
    LoadedTranscriptMessage,
)


@dataclass(frozen=True)
class HistoryToolCall:
    tool_call_id: str
    tool_name: str


@dataclass(frozen=True)
class HistoryAttachmentSummary:
    attachment_id: str
    attachment_type: str
    source: str
    summary: str


@dataclass(frozen=True)
class HistoryRecord:
    uuid: str
    parent_uuid: str | None
    role: str
    text: str
    tool_call_id: str | None = None
    tool_name: str | None = None
    is_error: bool = False
    tool_calls: tuple[HistoryToolCall, ...] = ()
    attachments: tuple[HistoryAttachmentSummary, ...] = ()
    source_uuid: str | None = None
    record_kind: str | None = None
    externalized: bool = False
    external_result_path: str | None = None
    missing_external_result: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationHistory:
    session_id: str
    records: tuple[HistoryRecord, ...] = ()
    diagnostics: tuple[str, ...] = ()


def load_conversation_history(
    transcript_store: JsonlTranscriptStore | InMemoryTranscriptStore,
) -> ConversationHistory:
    """Read chat history without touching external tool-result bodies."""

    loaded = transcript_store.load_messages(restore_external_results=False)
    if not loaded:
        return ConversationHistory(session_id=transcript_store.session_id)

    by_uuid = {record.uuid: record for record in loaded}
    active = select_active_chain(loaded)
    display = _display_sequence(active, loaded, by_uuid)
    attachments_by_user = _attachments_by_user(loaded)

    records: list[HistoryRecord] = []
    diagnostics: list[str] = []
    for record in display:
        role = record.message.get("role")
        if role == "user":
            records.append(
                _to_history_record(
                    record,
                    attachments=attachments_by_user.get(record.uuid, ()),
                )
            )
        else:
            records.append(_to_history_record(record))

    declared = {
        call.tool_call_id
        for record in records
        for call in record.tool_calls
    }
    for record in records:
        if record.role == "tool_result" and record.tool_call_id not in declared:
            diagnostics.append(f"unpaired_tool_result:{record.uuid}")

    return ConversationHistory(
        session_id=transcript_store.session_id,
        records=tuple(records),
        diagnostics=tuple(diagnostics),
    )


def _display_sequence(
    chain: tuple[LoadedTranscriptMessage, ...],
    loaded: tuple[LoadedTranscriptMessage, ...],
    by_uuid: dict[str, LoadedTranscriptMessage],
) -> list[LoadedTranscriptMessage]:
    result: list[LoadedTranscriptMessage] = []
    seen: set[str] = set()

    def add(record: LoadedTranscriptMessage) -> None:
        if record.uuid in seen:
            return
        if _is_internal(record):
            return
        seen.add(record.uuid)
        result.append(record)

    for record in chain:
        if _is_compaction_marker(record):
            before = tuple(
                item
                for item in loaded
                if item.sequence < record.sequence and item.uuid != record.uuid
            )
            if before:
                for item in _display_sequence(
                    select_active_chain(before),
                    before,
                    by_uuid,
                ):
                    add(item)
            continue
        if record.source_uuid and record.source_uuid in by_uuid:
            source = by_uuid[record.source_uuid]
            for item in _display_sequence(
                _ancestor_chain(source, by_uuid, seen),
                loaded,
                by_uuid,
            ):
                add(item)
            continue
        # Legacy compaction copies have no source_uuid: the boundary fallback
        # above already emitted their originals, so skip the copies by their
        # compaction metadata rather than by text comparison.
        if _is_compaction_product(record):
            continue
        add(record)
    return result


def _ancestor_chain(
    record: LoadedTranscriptMessage,
    by_uuid: dict[str, LoadedTranscriptMessage],
    stop_uuids: set[str],
) -> tuple[LoadedTranscriptMessage, ...]:
    chain: list[LoadedTranscriptMessage] = []
    seen: set[str] = set()
    current: LoadedTranscriptMessage | None = record
    while current is not None and current.uuid not in seen:
        if current.uuid in stop_uuids and chain:
            break
        seen.add(current.uuid)
        chain.append(current)
        current = _previous(current, by_uuid)
    chain.reverse()
    return tuple(chain)


def _previous(
    record: LoadedTranscriptMessage,
    by_uuid: dict[str, LoadedTranscriptMessage],
) -> LoadedTranscriptMessage | None:
    if record.source_uuid and record.source_uuid in by_uuid:
        return by_uuid[record.source_uuid]
    if record.parent_uuid and record.parent_uuid in by_uuid:
        return by_uuid[record.parent_uuid]
    return None


def _attachments_by_user(
    loaded: tuple[LoadedTranscriptMessage, ...],
) -> dict[str, tuple[LoadedTranscriptMessage, ...]]:
    grouped: dict[str, list[LoadedTranscriptMessage]] = {}
    last_user_uuid: str | None = None
    for record in loaded:
        role = record.message.get("role")
        if role == "user" and not _is_compaction_marker(record):
            last_user_uuid = record.uuid
            continue
        if role == "attachment" and last_user_uuid is not None:
            grouped.setdefault(last_user_uuid, []).append(record)
    return {uuid: tuple(items) for uuid, items in grouped.items()}


def _to_history_record(
    record: LoadedTranscriptMessage,
    *,
    attachments: tuple[LoadedTranscriptMessage, ...] = (),
) -> HistoryRecord:
    message = record.message
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    role = str(message.get("role", "unknown"))
    tool_calls = tuple(
        HistoryToolCall(tool_call_id=call_id, tool_name=name)
        for call_id, name in assistant_tool_call_ids(message)
    )
    return HistoryRecord(
        uuid=record.uuid,
        parent_uuid=record.parent_uuid,
        role=role,
        text=_text_content(message.get("content")),
        tool_call_id=_optional_str(message.get("tool_call_id")),
        tool_name=_optional_str(message.get("tool_name")),
        is_error=message.get("is_error") is True,
        tool_calls=tool_calls,
        attachments=tuple(_attachment_summary(item) for item in attachments),
        source_uuid=record.source_uuid,
        record_kind=record.record_kind,
        externalized=metadata.get("tool_result_externalized") is True,
        external_result_path=_optional_str(metadata.get("tool_result_path")),
        missing_external_result=metadata.get("missing_external_tool_result") is True,
        metadata=dict(metadata),
    )


def _attachment_summary(record: LoadedTranscriptMessage) -> HistoryAttachmentSummary:
    message = record.message
    attachment = message.get("attachment")
    attachment = attachment if isinstance(attachment, dict) else {}
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    attachment_type = str(
        attachment.get("type") or metadata.get("attachment_type") or "unknown"
    )
    attachment_id = str(
        metadata.get("attachment_id") or attachment.get("id") or record.uuid
    )
    return HistoryAttachmentSummary(
        attachment_id=attachment_id,
        attachment_type=attachment_type,
        source=str(metadata.get("source") or "unknown"),
        summary=_attachment_text(attachment_type, attachment),
    )


def _attachment_text(attachment_type: str, attachment: dict[str, Any]) -> str:
    if attachment_type == "file":
        return f"[attachment file] {attachment.get('path', '')}"
    if attachment_type == "directory":
        return f"[directory attachment] {attachment.get('path', '')}"
    if attachment_type == "skill":
        return f"[skill loaded] {attachment.get('skill_name', 'unknown')}"
    if attachment_type == "plan_mode":
        variant = attachment.get("variant", "intro")
        return f"[plan mode] {variant} {attachment.get('plan_path', '')}"
    if attachment_type == "edited_text_file":
        return f"[edited file] {attachment.get('path', '')}"
    return f"[{attachment_type} attachment]"


def _is_compaction_marker(record: LoadedTranscriptMessage) -> bool:
    metadata = record.message.get("metadata")
    if not isinstance(metadata, dict):
        return False
    return bool(
        metadata.get("is_compact_boundary") or metadata.get("is_compact_summary")
    )


def _is_compaction_product(record: LoadedTranscriptMessage) -> bool:
    metadata = record.message.get("metadata")
    if not isinstance(metadata, dict):
        return record.record_kind == "compaction"
    return "compaction" in metadata or record.record_kind == "compaction"


def _is_internal(record: LoadedTranscriptMessage) -> bool:
    if _is_compaction_marker(record):
        return True
    return record.message.get("role") == "attachment"


def _text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
