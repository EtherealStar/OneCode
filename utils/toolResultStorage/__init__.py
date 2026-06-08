"""Shared storage for durable tool result artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class StoredToolResultRef:
    result_id: str
    relative_path: str
    absolute_path: Path
    tool_call_id: str
    tool_name: str
    original_size_chars: int
    original_size_bytes: int


class ToolResultStorage:
    """Persist complete tool results under one session artifact directory."""

    def __init__(self, session_dir: Path | str) -> None:
        self._session_dir = Path(session_dir)
        self._results_dir = self._session_dir / "tool-results"

    @property
    def results_dir(self) -> Path:
        return self._results_dir

    def persist_tool_result(
        self,
        *,
        tool_call_id: Any,
        content: str,
        tool_name: str = "",
    ) -> StoredToolResultRef:
        """Write a complete tool result and return its stable durable reference."""

        self._results_dir.mkdir(parents=True, exist_ok=True)
        normalized_tool_call_id = tool_call_id if isinstance(tool_call_id, str) else ""
        result_id = _safe_result_id(normalized_tool_call_id)
        path = self._results_dir / f"{result_id}.txt"
        if path.exists():
            if _path_has_content(path, content):
                return self._ref(
                    result_id=result_id,
                    path=path,
                    tool_call_id=normalized_tool_call_id,
                    tool_name=tool_name,
                    content=content,
                )
            result_id, path = self._content_addressed_path(result_id, content)

        path.write_text(content, encoding="utf-8")
        return self._ref(
            result_id=result_id,
            path=path,
            tool_call_id=normalized_tool_call_id,
            tool_name=tool_name,
            content=content,
        )

    def read_result(self, relative_path: str) -> str:
        """Read a stored result by a session-relative path."""

        path = self._session_dir / relative_path
        return path.read_text(encoding="utf-8")

    def format_model_reference(
        self,
        ref: StoredToolResultRef,
        *,
        preview: str,
    ) -> str:
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

    def format_transcript_externalization(
        self,
        ref: StoredToolResultRef,
        *,
        preview: str,
    ) -> str:
        """Create the text persisted in JSONL when the full result is externalized."""

        return f"[tool result externalized: {ref.relative_path}]\n{preview}"

    def transcript_metadata(
        self,
        ref: StoredToolResultRef,
        *,
        preview_chars: int,
    ) -> dict[str, object]:
        """Return metadata used by transcript restore for an externalized result."""

        return {
            "tool_result_externalized": True,
            "tool_result_path": ref.relative_path,
            "tool_result_id": ref.result_id,
            "original_tool_call_id": ref.tool_call_id,
            "original_size_bytes": ref.original_size_bytes,
            "original_size_chars": ref.original_size_chars,
            "preview_chars": preview_chars,
        }

    def stored_result_metadata(
        self,
        ref: StoredToolResultRef,
        *,
        max_result_size_chars: int | float,
    ) -> dict[str, object]:
        """Return metadata used by model-visible tool result references."""

        return {
            "result_truncated": True,
            "result_stored": True,
            "original_size_chars": ref.original_size_chars,
            "max_result_size_chars": max_result_size_chars,
            "stored_result_id": ref.result_id,
            "stored_result_path": str(ref.absolute_path),
            "stored_result_relative_path": ref.relative_path,
        }

    def _content_addressed_path(
        self,
        base_result_id: str,
        content: str,
    ) -> tuple[str, Path]:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
        result_id = f"{base_result_id}-{content_hash}"
        path = self._results_dir / f"{result_id}.txt"
        suffix = 2
        while path.exists() and not _path_has_content(path, content):
            result_id = f"{base_result_id}-{content_hash}-{suffix}"
            path = self._results_dir / f"{result_id}.txt"
            suffix += 1
        return result_id, path

    def _ref(
        self,
        *,
        result_id: str,
        path: Path,
        tool_call_id: str,
        tool_name: str,
        content: str,
    ) -> StoredToolResultRef:
        return StoredToolResultRef(
            result_id=result_id,
            relative_path=f"tool-results/{path.name}",
            absolute_path=path.resolve(),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            original_size_chars=len(content),
            original_size_bytes=len(content.encode("utf-8")),
        )


def _safe_result_id(tool_call_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", tool_call_id).strip("._")
    return safe or "result"


def _path_has_content(path: Path, content: str) -> bool:
    try:
        return path.read_text(encoding="utf-8") == content
    except OSError:
        return False
