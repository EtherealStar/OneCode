"""Guarded filesystem glob search tool."""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolResultPolicy,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
    is_guard_policy_allowed,
)
from tools.glob.prompt import PROMPT


DEFAULT_HEAD_LIMIT = 100


class GlobInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    pattern: str
    path: str | None = None
    head_limit: int | None = Field(default=None, ge=0)
    offset: int = Field(default=0, ge=0)

    @field_validator("pattern")
    @classmethod
    def _pattern_not_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("pattern must not be empty.")
        return stripped

    @field_validator("path")
    @classmethod
    def _path_not_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("path must not be empty.")
        return value


INPUT_SCHEMA: dict[str, Any] = GlobInput.model_json_schema()


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="glob",
        description="Find files by pathname pattern in the local filesystem.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="find files by name pattern",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _parse_input(tool_input: dict[str, Any]) -> GlobInput:
    return GlobInput.model_validate(tool_input)


def _validate(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ValidationResult:
    try:
        parsed = _parse_input(tool_input)
    except ValidationError as exc:
        return ValidationResult.failure(_validation_message(exc))

    if runtime.guard is None or parsed.path is None:
        return ValidationResult.success()

    policy = runtime.guard.check_path(parsed.path, operation="list", kind="directory")
    if policy.action != "allow":
        return ValidationResult.success()
    if policy.normalized_path.exists() and not policy.normalized_path.is_dir():
        return ValidationResult.failure("path must be a directory.")
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    parsed = _parse_input(tool_input)
    root = parsed.path or "."
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(ToolTarget(kind="directory", operation="list", value=root),),
        result_policy=ToolResultPolicy(
            max_result_size_chars=100_000,
            persist_when_exceeded=False,
            preview_chars=4_000,
        ),
        permission_subject=f"glob:{root}:{parsed.pattern}",
    )


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("glob requires a sandbox guard.")

    parsed = _parse_input(tool_input)
    root_input = parsed.path or "."
    root_policy = runtime.guard.check_path(
        root_input,
        operation="list",
        kind="directory",
    )
    if not is_guard_policy_allowed(root_policy, runtime):
        return _guard_error(root_policy)

    root = root_policy.normalized_path
    if not root.exists():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="glob",
            content=f"Directory does not exist: {root}",
            is_error=True,
            metadata={"error": "directory_not_found", "path": str(root)},
        )
    if not root.is_dir():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="glob",
            content=f"Path is not a directory: {root}",
            is_error=True,
            metadata={"error": "path_not_directory", "path": str(root)},
        )

    candidates: list[Path] = []
    filtered_count = 0
    guard_cache: dict[Path, bool] = {}

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative_to_root = _slash_path(path.relative_to(root))
        if not fnmatch.fnmatchcase(relative_to_root, parsed.pattern):
            continue
        if not _path_allowed(path, runtime, guard_cache):
            filtered_count += 1
            continue
        candidates.append(path)

    def sort_key(path: Path) -> tuple[float, str]:
        return (-_mtime(path), _display_path(path, runtime))

    candidates = [
        path
        for _, path in sorted((sort_key(path), path) for path in candidates)
    ]
    total_matches = len(candidates)
    limit = _effective_limit(parsed.head_limit, DEFAULT_HEAD_LIMIT)
    selected = candidates[parsed.offset :] if limit is None else candidates[
        parsed.offset : parsed.offset + limit
    ]
    truncated = parsed.offset + len(selected) < total_matches

    lines = [f"Found {total_matches} files"]
    lines.extend(_display_path(path, runtime) for path in selected)
    if truncated or parsed.offset:
        limit_label = "unlimited" if limit is None else str(limit)
        lines.append("")
        lines.append(
            f"[Showing results with pagination = offset: {parsed.offset}, limit: {limit_label}]"
        )

    return ToolExecutionResult(
        tool_call_id="",
        tool_name="glob",
        content="\n".join(lines),
        metadata={
            "num_files": len(selected),
            "total_matches_before_pagination": total_matches,
            "filtered_count": filtered_count,
            "applied_limit": limit,
            "applied_offset": parsed.offset,
            "truncated": truncated,
            "path": str(root),
        },
    )


def _path_allowed(
    path: Path,
    runtime: ToolRuntime,
    cache: dict[Path, bool],
) -> bool:
    try:
        key = path.resolve(strict=False)
    except OSError:
        return False
    cached = cache.get(key)
    if cached is not None:
        return cached
    if runtime.guard is None:
        return False
    try:
        allowed = is_guard_policy_allowed(
            runtime.guard.check_path(key, operation="read", kind="file"),
            runtime,
        )
    except Exception:
        allowed = False
    cache[key] = allowed
    return allowed


def _display_path(path: Path, runtime: ToolRuntime) -> str:
    root = runtime.guard.boundary.cwd if runtime.guard is not None else Path.cwd()
    try:
        return _slash_path(path.resolve(strict=False).relative_to(root))
    except ValueError:
        return _slash_path(path.resolve(strict=False))


def _slash_path(path: Path) -> str:
    return path.as_posix()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _effective_limit(value: int | None, default: int) -> int | None:
    if value is None:
        return default
    if value == 0:
        return None
    return value


def _guard_error(policy) -> ToolExecutionResult:
    payload = policy.to_tool_error()
    if policy.action == "ask":
        payload["error"] = "path_guard_ask_required"
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="glob",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": payload["error"]},
    )


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
