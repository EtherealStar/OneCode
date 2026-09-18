"""用于可恢复活动消息链的中立 transcript 恢复服务。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from services.context.message_shapes import (
    assistant_tool_call_ids,
    message_has_body,
    prune_assistant_declarations,
)
from services.context.transcript import JsonlTranscriptStore, LoadedTranscriptMessage


@dataclass(frozen=True)
class RestoredTranscript:
    session_id: str
    messages: tuple[dict[str, Any], ...]
    last_uuid: str | None
    records: tuple[LoadedTranscriptMessage, ...] = ()
    warnings: tuple[str, ...] = ()


def restore_transcript_active_chain(
    transcript_store: JsonlTranscriptStore,
) -> RestoredTranscript:
    """从仅追加的 transcript 中恢复最新的活动链。

    transcript 以仅追加方式存储每一个历史分支。恢复时必须仅向模型提供当前链，
    随后修复工具调用配对，避免 provider adapter 接收到孤立或中断的工具序列。
    未配对的声明会被丢弃；孤立的结果会从内存以及真实的 transcript 中移除，
    而不是替换为合成结果。
    """

    loaded = transcript_store.load_messages()
    if not loaded:
        return RestoredTranscript(
            session_id=transcript_store.session_id,
            messages=(),
            last_uuid=None,
            records=(),
        )

    chain = select_active_chain(loaded)
    kept, deleted, modified, warnings = _sanitize_chain(chain)
    if deleted or modified:
        _repair_transcript(transcript_store, kept, deleted)
    messages = tuple(record.message for record in kept)
    return RestoredTranscript(
        session_id=chain[-1].session_id if chain else loaded[-1].session_id,
        messages=messages,
        last_uuid=kept[-1].uuid if kept else None,
        records=kept,
        warnings=tuple(warnings),
    )


def _repair_transcript(
    transcript_store: JsonlTranscriptStore,
    kept: list[LoadedTranscriptMessage],
    deleted: set[str],
) -> None:
    overrides = {
        record.uuid: record
        for record in kept
        if record.message.get("role") == "assistant"
    }
    raw = transcript_store.read_records()
    repaired: list[LoadedTranscriptMessage] = []
    for record in raw:
        if record.uuid in deleted:
            continue
        repaired.append(overrides.get(record.uuid, record))
    transcript_store.rewrite_records(repaired)


def select_active_chain(
    loaded: tuple[LoadedTranscriptMessage, ...],
) -> tuple[LoadedTranscriptMessage, ...]:
    by_uuid = {item.uuid: item for item in loaded}
    parent_uuids = {
        item.parent_uuid for item in loaded if isinstance(item.parent_uuid, str)
    }
    leaves = [item for item in loaded if item.uuid not in parent_uuids]
    if not leaves:
        leaves = [loaded[-1]]

    non_attachment_leaves = [
        item for item in leaves if item.message.get("role") != "attachment"
    ]
    leaf = max(non_attachment_leaves or leaves, key=_leaf_sort_key)

    chain: list[LoadedTranscriptMessage] = []
    seen: set[str] = set()
    current: LoadedTranscriptMessage | None = leaf
    while current is not None and current.uuid not in seen:
        chain.append(current)
        seen.add(current.uuid)
        parent_uuid = current.parent_uuid
        current = by_uuid.get(parent_uuid) if parent_uuid else None
    chain.reverse()
    return tuple(chain)


def _leaf_sort_key(item: LoadedTranscriptMessage) -> tuple[int, float, int]:
    timestamp = _parse_timestamp(item.timestamp)
    if timestamp is None:
        return (0, 0.0, item.sequence)
    return (1, timestamp.timestamp(), item.sequence)


def _sanitize_chain(
    chain: tuple[LoadedTranscriptMessage, ...],
) -> tuple[list[LoadedTranscriptMessage], set[str], set[str], list[str]]:
    kept: list[LoadedTranscriptMessage] = []
    warnings: list[str] = []
    deleted: set[str] = set()
    modified: set[str] = set()
    index = 0
    while index < len(chain):
        item = chain[index]
        role = item.message.get("role")

        if role == "assistant":
            declarations = dict(assistant_tool_call_ids(item.message))
            matched: dict[str, LoadedTranscriptMessage] = {}
            index += 1
            while index < len(chain) and chain[index].message.get("role") == "tool_result":
                result_item = chain[index]
                tool_call_id = result_item.message.get("tool_call_id")
                if (
                    isinstance(tool_call_id, str)
                    and tool_call_id in declarations
                    and tool_call_id not in matched
                ):
                    matched[tool_call_id] = result_item.with_message(
                        {
                            **result_item.message,
                            "tool_name": result_item.message.get("tool_name")
                            or declarations[tool_call_id]
                            or "unknown_tool",
                        }
                    )
                else:
                    warnings.append(f"dropped_orphan_tool_result:{result_item.uuid}")
                    deleted.add(result_item.uuid)
                index += 1

            pruned_message = prune_assistant_declarations(
                item.message,
                set(matched),
            )
            if not message_has_body(pruned_message):
                warnings.append(f"dropped_blank_assistant:{item.uuid}")
                deleted.add(item.uuid)
                for matched_item in matched.values():
                    deleted.add(matched_item.uuid)
                    warnings.append(
                        f"dropped_orphan_tool_result:{matched_item.uuid}"
                    )
                continue
            for call_id in declarations:
                if call_id not in matched:
                    warnings.append(f"dropped_unpaired_tool_call:{call_id}")
            if pruned_message != item.message:
                modified.add(item.uuid)
            kept.append(item.with_message(pruned_message))
            kept.extend(matched.values())
            continue

        if role == "tool_result":
            warnings.append(f"dropped_orphan_tool_result:{item.uuid}")
            deleted.add(item.uuid)
            index += 1
            continue

        kept.append(item)
        index += 1

    return kept, deleted, modified, warnings


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
