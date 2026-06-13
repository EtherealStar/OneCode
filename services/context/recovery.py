"""Provider-neutral transcript recovery for resumable active message chains."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from services.context.transcript import JsonlTranscriptStore, LoadedTranscriptMessage


@dataclass(frozen=True)
class RestoredTranscript:
    session_id: str
    messages: tuple[dict[str, Any], ...]
    last_uuid: str | None
    warnings: tuple[str, ...] = ()


def restore_transcript_active_chain(
    transcript_store: JsonlTranscriptStore,
) -> RestoredTranscript:
    """Restore the latest active chain from an append-only transcript.

    The transcript stores every historical branch append-only. Resume must feed
    the model only the current chain, then repair tool-call pairing so provider
    adapters do not receive orphaned or interrupted tool sequences.
    """

    loaded = transcript_store.load_messages()
    if not loaded:
        return RestoredTranscript(
            session_id=transcript_store.session_id,
            messages=(),
            last_uuid=None,
        )

    chain = _select_active_chain(loaded)
    messages, last_uuid, warnings = _sanitize_chain(chain)
    return RestoredTranscript(
        session_id=chain[-1].session_id if chain else loaded[-1].session_id,
        messages=messages,
        last_uuid=last_uuid,
        warnings=tuple(warnings),
    )


def _select_active_chain(
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
) -> tuple[tuple[dict[str, Any], ...], str | None, list[str]]:
    restored: list[dict[str, Any]] = []
    warnings: list[str] = []
    last_uuid: str | None = None
    index = 0
    while index < len(chain):
        item = chain[index]
        message = deepcopy(item.message)
        role = message.get("role")

        if role == "assistant":
            call_ids = _assistant_tool_call_ids(message)
            if not call_ids and _is_blank_content(message.get("content")):
                warnings.append(f"dropped_blank_assistant:{item.uuid}")
                index += 1
                continue
            restored.append(message)
            last_uuid = item.uuid
            if not call_ids:
                index += 1
                continue

            expected = dict(call_ids)
            matched: set[str] = set()
            index += 1
            while index < len(chain) and chain[index].message.get("role") == "tool_result":
                result_item = chain[index]
                result = deepcopy(result_item.message)
                tool_call_id = result.get("tool_call_id")
                if isinstance(tool_call_id, str) and tool_call_id in expected and tool_call_id not in matched:
                    result.setdefault(
                        "tool_name",
                        expected[tool_call_id]
                        or result.get("tool_name")
                        or "unknown_tool",
                    )
                    restored.append(result)
                    matched.add(tool_call_id)
                    last_uuid = result_item.uuid
                else:
                    warnings.append(f"dropped_orphan_tool_result:{result_item.uuid}")
                index += 1
            for tool_call_id, tool_name in expected.items():
                if tool_call_id in matched:
                    continue
                restored.append(_synthetic_interrupted_tool_result(tool_call_id, tool_name))
                warnings.append(f"inserted_interrupted_tool_result:{tool_call_id}")
            continue

        if role == "tool_result":
            warnings.append(f"dropped_orphan_tool_result:{item.uuid}")
            index += 1
            continue

        restored.append(message)
        last_uuid = item.uuid
        index += 1

    return tuple(restored), last_uuid, warnings


def _assistant_tool_call_ids(message: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    ids: list[tuple[str, str]] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, list):
        for call in raw_calls:
            if not isinstance(call, dict):
                continue
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                continue
            name = call.get("name")
            function = call.get("function")
            if not isinstance(name, str) and isinstance(function, dict):
                function_name = function.get("name")
                if isinstance(function_name, str):
                    name = function_name
            ids.append((call_id, name if isinstance(name, str) else "unknown_tool"))

    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            call_id = block.get("id")
            if not isinstance(call_id, str) or not call_id:
                continue
            name = block.get("name")
            ids.append((call_id, name if isinstance(name, str) else "unknown_tool"))
    return tuple(ids)


def _synthetic_interrupted_tool_result(
    tool_call_id: str,
    tool_name: str,
) -> dict[str, Any]:
    return {
        "role": "tool_result",
        "tool_call_id": tool_call_id,
        "tool_name": tool_name or "unknown_tool",
        "content": json.dumps(
            {
                "error": "interrupted_tool_call",
                "message": "Tool call was interrupted before a result was recorded.",
            },
            ensure_ascii=False,
        ),
        "is_error": True,
        "metadata": {"error": "interrupted_tool_call", "synthetic": True},
    }


def _is_blank_content(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        return not content
    return False


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
