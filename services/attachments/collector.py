"""Collect attachments for a user turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from core.runtime_state import RuntimeState
from services.attachments.parser import extract_at_mentions
from services.attachments.resolver import (
    ResolutionError,
    ResolvedMention,
    resolve_mention,
)
from services.attachments.types import AttachmentMessage, AttachmentScope
from services.guard import GuardPolicy, SandboxGuard
from services.permissions import PermissionPolicy, PermissionPrompter
from services.permissions.types import PermissionResponse
from services.tools.file_state import FileStateCache
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolTarget,
)
from utils.text_io import DEFAULT_TEXT_ENCODING, read_text_file


MAX_DIRECTORY_ENTRIES = 1_000
MAX_FULL_ATTACHMENT_CHARS = 200_000


class QueuedAttachmentSource(Protocol):
    def collect(self, state: RuntimeState) -> tuple[dict[str, Any], ...]:
        ...


@dataclass(frozen=True)
class ReadResult:
    ok: bool
    content: str = ""
    error: str | None = None
    truncated: bool = False
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class DirectoryResult:
    ok: bool
    entries: tuple[str, ...] = ()
    error: str | None = None
    truncated: bool = False
    metadata: dict[str, Any] | None = None


class AttachmentFileReader:
    def __init__(
        self,
        *,
        guard: SandboxGuard,
        permission_policy: PermissionPolicy,
        permission_prompter: PermissionPrompter | None = None,
    ) -> None:
        self.guard = guard
        self.permission_policy = permission_policy
        self.permission_prompter = permission_prompter
        self._descriptor = ToolDescriptor(
            name="read_file",
            description="Read attachment text from the local filesystem.",
            input_schema={"type": "object"},
            handler=lambda _tool_input, _runtime: None,  # type: ignore[arg-type,return-value]
        )

    async def read_text(
        self,
        path: Path,
        *,
        offset: int | None,
        limit: int | None,
        state: RuntimeState | None = None,
    ) -> ReadResult:
        """Read UTF-8 text only after guard and permission policy agree."""

        state = state or RuntimeState()
        guard_policy = self.guard.check_path(path, operation="read", kind="file")
        decision = await self._permission_decision(
            path,
            state,
            guard_policy,
            operation="read",
            kind="file",
        )
        if decision is not None:
            return ReadResult(ok=False, error=decision)
        if path.is_dir():
            return ReadResult(ok=False, error="path_is_directory")
        if not path.exists():
            return ReadResult(ok=False, error="file_not_found")
        if offset is None and limit is None:
            try:
                text, truncated = _read_text_with_char_limit(
                    path,
                    max_chars=MAX_FULL_ATTACHMENT_CHARS,
                )
            except OSError as exc:
                return ReadResult(ok=False, error=type(exc).__name__)
            return ReadResult(
                ok=True,
                content=text,
                truncated=truncated,
                metadata={"path": str(path), "max_chars": MAX_FULL_ATTACHMENT_CHARS},
            )
        try:
            numbered = _read_numbered_lines(path, offset=offset or 1, limit=limit)
        except OSError as exc:
            return ReadResult(ok=False, error=type(exc).__name__)
        return ReadResult(
            ok=True,
            content=numbered,
            metadata={"path": str(path), "offset": offset, "limit": limit},
        )

    async def list_directory(
        self,
        path: Path,
        *,
        state: RuntimeState,
    ) -> DirectoryResult:
        """List a directory through the same read/list permission path."""

        guard_policy = self.guard.check_path(path, operation="list", kind="directory")
        decision = await self._permission_decision(
            path,
            state,
            guard_policy,
            operation="list",
            kind="directory",
        )
        if decision is not None:
            return DirectoryResult(ok=False, error=decision)
        if not path.exists():
            return DirectoryResult(ok=False, error="directory_not_found")
        if not path.is_dir():
            return DirectoryResult(ok=False, error="path_is_not_directory")
        try:
            names, truncated = _bounded_directory_names(path)
        except OSError as exc:
            return DirectoryResult(ok=False, error=type(exc).__name__)
        return DirectoryResult(
            ok=True,
            entries=tuple(names),
            truncated=truncated,
            metadata={
                "path": str(path),
                "entry_count": len(names),
                "entry_count_truncated": truncated,
            },
        )

    async def _permission_decision(
        self,
        path: Path,
        state: RuntimeState,
        guard_policy: GuardPolicy,
        *,
        operation: str,
        kind: str,
    ) -> str | None:
        classification = ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(ToolTarget(kind=kind, operation=operation, value=str(path)),),
            permission_subject=f"attachment:{path}",
        )
        tool_call = ToolCall(
            id=f"attachment_{operation}",
            name=self._descriptor.name,
            input={"file_path": str(path)},
        )
        decision = self.permission_policy.evaluate(
            tool_call=tool_call,
            descriptor=self._descriptor,
            classification=classification,
            guard_policies=(guard_policy,),
            state=state,
        )
        if decision.action == "allow":
            return None
        if decision.action == "ask" and self.permission_prompter is not None:
            request = self.permission_policy.request_for_decision(
                tool_call=tool_call,
                descriptor=self._descriptor,
                classification=classification,
                decision=decision,
                tool_input={"file_path": str(path)},
            )
            response = await self.permission_prompter.request_permission(request)
            if isinstance(response, PermissionResponse) and response.action == "allow":
                self.permission_policy.record_response(request, response)
                return None
        return decision.action


class AttachmentCollector:
    def __init__(
        self,
        *,
        workspace: Path,
        reader: AttachmentFileReader,
        file_state_cache: FileStateCache | None = None,
        shared_sources: tuple[QueuedAttachmentSource, ...] = (),
    ) -> None:
        self.workspace = workspace.resolve()
        self.reader = reader
        self.file_state_cache = file_state_cache or FileStateCache()
        self.shared_sources = shared_sources

    async def collect_for_user_turn(
        self,
        prompt: str,
        state: RuntimeState,
        messages: tuple[dict[str, Any], ...],
        *,
        is_main_thread: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        """Collect user, shared, then main-thread-only attachment messages."""

        _ = messages
        payloads: list[tuple[dict[str, Any], AttachmentScope, str]] = []
        for mention in extract_at_mentions(prompt):
            resolved = resolve_mention(mention, self.workspace)
            if isinstance(resolved, ResolutionError):
                payloads.append(
                    (
                        _resolution_error_payload(resolved),
                        AttachmentScope.MAIN_THREAD,
                        "user_input",
                    )
                )
                continue
            payload = await self._payload_for_resolved(resolved, state)
            payloads.append((payload, AttachmentScope.MAIN_THREAD, "user_input"))

        for source in self.shared_sources:
            for payload in source.collect(state):
                if payload.get("type") == "todo_reminder":
                    continue
                payloads.append((payload, AttachmentScope.SHARED, "runtime"))

        if is_main_thread:
            for changed in self.file_state_cache.changed_text_files():
                payloads.append(
                    (
                        {
                            "type": "edited_text_file",
                            "path": str(changed.path),
                            "diff": changed.diff,
                        },
                        AttachmentScope.MAIN_THREAD,
                        "file_state",
                    )
                )

        return tuple(
            AttachmentMessage(
                attachment=payload,
                scope=scope,
                source=source,
            ).to_message()
            for payload, scope, source in payloads
        )

    async def _payload_for_resolved(
        self,
        resolved: ResolvedMention,
        state: RuntimeState,
    ) -> dict[str, Any]:
        if resolved.is_directory:
            result = await self.reader.list_directory(resolved.path, state=state)
            if not result.ok:
                return _read_error_payload(resolved, result.error or "read_failed")
            return {
                "type": "directory",
                "path": str(resolved.path),
                "entries": list(result.entries),
                "truncated": result.truncated,
                "mention": resolved.mention.raw,
            }

        offset = resolved.mention.line_start
        limit = None
        if (
            resolved.mention.line_start is not None
            and resolved.mention.line_end is not None
        ):
            limit = resolved.mention.line_end - resolved.mention.line_start + 1
        result = await self.reader.read_text(
            resolved.path,
            offset=offset,
            limit=limit,
            state=state,
        )
        if not result.ok:
            return _read_error_payload(resolved, result.error or "read_failed")
        if offset is None and limit is None and not result.truncated:
            self.file_state_cache.snapshot_path(resolved.path)
        return {
            "type": "file",
            "path": str(resolved.path),
            "content": result.content,
            "offset": offset or 1,
            "limit": limit,
            "truncated": result.truncated,
            "mention": resolved.mention.raw,
        }


def _read_numbered_lines(
    path: Path,
    *,
    offset: int,
    limit: int | None,
) -> str:
    selected: list[tuple[int, str]] = []
    last_line = None if limit is None else offset + limit - 1
    with path.open(
        "r",
        encoding=DEFAULT_TEXT_ENCODING,
        errors="replace",
    ) as handle:
        for line_number, line in enumerate(handle, start=1):
            if line_number < offset:
                continue
            if last_line is not None and line_number > last_line:
                break
            selected.append((line_number, line.rstrip("\r\n")))
    return "\n".join(
        f"{line_number}\t{line}"
        for line_number, line in selected
    )


def _read_text_with_char_limit(path: Path, *, max_chars: int) -> tuple[str, bool]:
    chunks: list[str] = []
    remaining = max_chars
    truncated = False
    with path.open(
        "r",
        encoding=DEFAULT_TEXT_ENCODING,
        errors="replace",
    ) as handle:
        while remaining > 0:
            chunk = handle.read(min(8192, remaining))
            if chunk == "":
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0 and handle.read(1) != "":
            truncated = True
    content = "".join(chunks)
    if truncated:
        content += "\n[attachment truncated]"
    return content, truncated


def _bounded_directory_names(path: Path) -> tuple[list[str], bool]:
    names: list[str] = []
    truncated = False
    for child in path.iterdir():
        if len(names) >= MAX_DIRECTORY_ENTRIES:
            truncated = True
            break
        names.append(child.name)
    names.sort(key=str.casefold)
    return names, truncated


def _resolution_error_payload(error: ResolutionError) -> dict[str, Any]:
    return {
        "type": "attachment_error",
        "error": error.error,
        "message": error.message,
        "mention": error.mention.raw,
        "path_text": error.mention.path_text,
        "candidates": list(error.candidates),
    }


def _read_error_payload(resolved: ResolvedMention, error: str) -> dict[str, Any]:
    return {
        "type": "attachment_error",
        "error": error,
        "mention": resolved.mention.raw,
        "path": str(resolved.path),
    }
