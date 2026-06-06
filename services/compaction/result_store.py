"""Durable storage for oversized tool results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import uuid


@dataclass(frozen=True)
class StoredResultRef:
    result_id: str
    relative_path: str
    absolute_path: Path
    tool_call_id: str
    tool_name: str
    original_size_chars: int


class ToolResultStore:
    """Persist large tool results under one session's transcript directory."""

    def __init__(self, session_dir: Path | str) -> None:
        self._session_dir = Path(session_dir)
        self._results_dir = self._session_dir / "tool-results"

    @property
    def results_dir(self) -> Path:
        return self._results_dir

    def persist_tool_result(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        content: str,
    ) -> StoredResultRef:
        """Write a complete tool result and return a model-safe reference."""

        result_id = _safe_result_id(tool_call_id)
        path = self._results_dir / f"{result_id}.txt"
        if path.exists():
            result_id = f"{result_id}-{uuid.uuid4().hex[:8]}"
            path = self._results_dir / f"{result_id}.txt"

        self._results_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return StoredResultRef(
            result_id=result_id,
            relative_path=f"tool-results/{path.name}",
            absolute_path=path.resolve(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            original_size_chars=len(content),
        )

    def format_model_reference(self, ref: StoredResultRef, *, preview: str) -> str:
        """Create the compact text shown to the model for a stored result."""

        return (
            "[Tool result stored]\n"
            f"Tool: {ref.tool_name}\n"
            f"Tool call id: {ref.tool_call_id}\n"
            f"Result id: {ref.result_id}\n"
            f"Path: {ref.absolute_path}\n"
            f"Relative path: {ref.relative_path}\n"
            f"Original size chars: {ref.original_size_chars}\n\n"
            "Preview:\n"
            f"{preview}\n\n"
            "To inspect the full result, read the stored path with a read-only file tool."
        )


def _safe_result_id(tool_call_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_call_id).strip("._")
    return safe or uuid.uuid4().hex
