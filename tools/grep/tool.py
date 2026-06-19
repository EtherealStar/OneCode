"""Guarded ripgrep-backed content search tool."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Literal, Protocol

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

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
from tools.grep.prompt import PROMPT
from utils.text_io import decode_text


DEFAULT_HEAD_LIMIT = 250
VCS_EXCLUDES = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")


@dataclass(frozen=True)
class RipgrepResult:
    returncode: int
    stdout: str
    stderr: str


class RipgrepRunner(Protocol):
    def run(self, args: list[str], cwd: Path) -> RipgrepResult:
        ...


class SubprocessRipgrepRunner:
    def run(self, args: list[str], cwd: Path) -> RipgrepResult:
        executable = shutil.which("rg")
        if executable is None:
            raise FileNotFoundError("ripgrep executable 'rg' was not found on PATH.")
        completed = subprocess.run(
            [executable, *args],
            cwd=cwd,
            capture_output=True,
            check=False,
        )
        return RipgrepResult(
            returncode=completed.returncode,
            stdout=decode_text(completed.stdout),
            stderr=decode_text(completed.stderr),
        )


class GrepInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        populate_by_name=True,
    )

    pattern: str
    path: str | None = None
    glob: str | None = None
    output_mode: Literal["content", "files_with_matches", "count"] = (
        "files_with_matches"
    )
    before: int | None = Field(default=None, alias="-B", ge=0)
    after: int | None = Field(default=None, alias="-A", ge=0)
    context_flag: int | None = Field(default=None, alias="-C", ge=0)
    context: int | None = Field(default=None, ge=0)
    show_line_numbers: bool | None = Field(default=None, alias="-n")
    case_insensitive: bool = Field(default=False, alias="-i")
    type: str | None = None
    head_limit: int | None = Field(default=None, ge=0)
    offset: int = Field(default=0, ge=0)
    multiline: bool = False

    @field_validator("pattern", "glob", "type", "path")
    @classmethod
    def _non_empty_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be empty.")
        return value.strip() if value is not None else value

    @model_validator(mode="after")
    def _context_only_for_content(self) -> "GrepInput":
        has_context = any(
            value is not None
            for value in (
                self.before,
                self.after,
                self.context_flag,
                self.context,
            )
        )
        if self.output_mode != "content" and has_context:
            raise ValueError("context options are only valid in content mode.")
        return self


INPUT_SCHEMA: dict[str, Any] = GrepInput.model_json_schema(by_alias=True)


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        name="grep",
        description="Search file contents with ripgrep.",
        input_schema=INPUT_SCHEMA,
        handler=_handle,
        prompt=PROMPT,
        search_hint="search file contents with regex",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _parse_input(tool_input: dict[str, Any]) -> GrepInput:
    return GrepInput.model_validate(tool_input)


def _validate(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ValidationResult:
    try:
        _parse_input(tool_input)
    except ValidationError as exc:
        return ValidationResult.failure(_validation_message(exc))
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    parsed = _parse_input(tool_input)
    target = parsed.path or "."
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(ToolTarget(kind="directory", operation="read", value=target),),
        result_policy=ToolResultPolicy(
            max_result_size_chars=20_000,
            persist_when_exceeded=True,
            preview_chars=4_000,
        ),
        permission_subject=f"grep:{target}:{parsed.pattern}",
    )


def _handle(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolExecutionResult:
    return _handle_with_runner(tool_input, runtime, SubprocessRipgrepRunner())


def _handle_with_runner(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
    runner: RipgrepRunner,
) -> ToolExecutionResult:
    if runtime.guard is None:
        raise RuntimeError("grep requires a sandbox guard.")

    parsed = _parse_input(tool_input)
    path_input = parsed.path or "."
    path_policy = runtime.guard.check_path(
        path_input,
        operation="read",
        kind="directory",
    )
    if not is_guard_policy_allowed(path_policy, runtime):
        return _guard_error(path_policy)

    target = path_policy.normalized_path
    if not target.exists():
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="grep",
            content=f"Path does not exist: {target}",
            is_error=True,
            metadata={"error": "path_not_found", "path": str(target)},
        )

    cwd = target if target.is_dir() else target.parent
    search_target = "." if target.is_dir() else target.name
    args = _build_rg_args(parsed, search_target)

    try:
        rg_result = runner.run(args, cwd)
    except FileNotFoundError as exc:
        return _error_result("ripgrep_not_found", str(exc))
    except Exception as exc:
        return _error_result("ripgrep_error", str(exc))

    if rg_result.returncode == 1:
        return _no_matches_result(parsed)
    if rg_result.returncode != 0:
        message = rg_result.stderr.strip() or f"ripgrep exited with {rg_result.returncode}"
        return _error_result(
            "ripgrep_error",
            message,
            {"returncode": rg_result.returncode, "stderr": rg_result.stderr},
        )

    guard_cache: dict[Path, bool] = {}
    if parsed.output_mode == "files_with_matches":
        return _files_with_matches_result(rg_result.stdout, cwd, parsed, runtime, guard_cache)
    if parsed.output_mode == "count":
        return _count_result(rg_result.stdout, cwd, parsed, runtime, guard_cache)
    return _content_result(rg_result.stdout, cwd, parsed, runtime, guard_cache)


def _build_rg_args(parsed: GrepInput, search_target: str) -> list[str]:
    args = [
        "--hidden",
        "--max-columns",
        "500",
        "--color",
        "never",
        "--no-heading",
        "--with-filename",
    ]
    for directory in VCS_EXCLUDES:
        args.extend(["--glob", f"!{directory}/**"])

    if parsed.output_mode == "files_with_matches":
        args.append("-l")
    elif parsed.output_mode == "count":
        args.append("-c")
    else:
        show_line_numbers = True if parsed.show_line_numbers is None else parsed.show_line_numbers
        if show_line_numbers:
            args.append("-n")
        context_value = parsed.context if parsed.context is not None else parsed.context_flag
        if context_value is not None:
            args.extend(["-C", str(context_value)])
        else:
            if parsed.before is not None:
                args.extend(["-B", str(parsed.before)])
            if parsed.after is not None:
                args.extend(["-A", str(parsed.after)])

    if parsed.case_insensitive:
        args.append("-i")
    if parsed.multiline:
        args.extend(["-U", "--multiline-dotall"])
    if parsed.type is not None:
        args.extend(["--type", parsed.type])
    if parsed.glob is not None:
        for pattern in _split_globs(parsed.glob):
            args.extend(["--glob", pattern])

    args.extend(["-e", parsed.pattern, search_target])
    return args


def _files_with_matches_result(
    output: str,
    cwd: Path,
    parsed: GrepInput,
    runtime: ToolRuntime,
    guard_cache: dict[Path, bool],
) -> ToolExecutionResult:
    filtered = 0
    paths: list[Path] = []
    for raw in output.splitlines():
        path = _resolve_rg_path(raw, cwd)
        if path is None or not _path_allowed(path, runtime, guard_cache):
            filtered += 1
            continue
        paths.append(path)

    def sort_path(path: Path) -> tuple[float, str]:
        return (-_mtime(path), _display_path(path, runtime))

    unique_paths = [
        path
        for _, path in sorted((sort_path(path), path) for path in set(paths))
    ]
    total = len(unique_paths)
    selected, limit, truncated = _paginate(unique_paths, parsed)

    lines = [f"Found {total} files"]
    lines.extend(_display_path(path, runtime) for path in selected)
    _append_pagination(lines, parsed.offset, limit, truncated)
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="grep",
        content="\n".join(lines),
        metadata={
            "mode": "files_with_matches",
            "num_files": len(selected),
            "filtered_count": filtered,
            "applied_limit": limit,
            "applied_offset": parsed.offset,
            "truncated": truncated,
        },
    )


def _count_result(
    output: str,
    cwd: Path,
    parsed: GrepInput,
    runtime: ToolRuntime,
    guard_cache: dict[Path, bool],
) -> ToolExecutionResult:
    filtered = 0
    entries: list[tuple[Path, int]] = []
    for raw in output.splitlines():
        path_text, count_text = raw.rsplit(":", 1) if ":" in raw else (raw, "0")
        path = _resolve_rg_path(path_text, cwd)
        if path is None or not _path_allowed(path, runtime, guard_cache):
            filtered += 1
            continue
        try:
            count = int(count_text)
        except ValueError:
            filtered += 1
            continue
        if count > 0:
            entries.append((path, count))

    def sort_entry(item: tuple[Path, int]) -> tuple[float, str]:
        return (-_mtime(item[0]), _display_path(item[0], runtime))

    entries = [
        entry
        for _, entry in sorted((sort_entry(entry), entry) for entry in entries)
    ]
    total_matches = sum(count for _, count in entries)
    selected, limit, truncated = _paginate(entries, parsed)

    lines = [f"Found {total_matches} matches in {len(entries)} files"]
    lines.extend(f"{_display_path(path, runtime)}:{count}" for path, count in selected)
    _append_pagination(lines, parsed.offset, limit, truncated)
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="grep",
        content="\n".join(lines),
        metadata={
            "mode": "count",
            "num_files": len(selected),
            "num_matches": total_matches,
            "filtered_count": filtered,
            "applied_limit": limit,
            "applied_offset": parsed.offset,
            "truncated": truncated,
        },
    )


def _content_result(
    output: str,
    cwd: Path,
    parsed: GrepInput,
    runtime: ToolRuntime,
    guard_cache: dict[Path, bool],
) -> ToolExecutionResult:
    filtered = 0
    kept: list[str] = []
    matched_paths: set[Path] = set()
    for raw in output.splitlines():
        path_text = _extract_content_path(raw)
        path = _resolve_rg_path(path_text, cwd) if path_text is not None else None
        if path is None or not _path_allowed(path, runtime, guard_cache):
            filtered += 1
            continue
        matched_paths.add(path)
        kept.append(_replace_line_prefix(raw, path_text, _display_path(path, runtime)))

    selected, limit, truncated = _paginate(kept, parsed)
    lines = list(selected)
    _append_pagination(lines, parsed.offset, limit, truncated)
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="grep",
        content="\n".join(lines),
        metadata={
            "mode": "content",
            "num_files": len(matched_paths),
            "num_lines": len(selected),
            "total_lines_before_pagination": len(kept),
            "filtered_count": filtered,
            "applied_limit": limit,
            "applied_offset": parsed.offset,
            "truncated": truncated,
        },
    )


def _no_matches_result(parsed: GrepInput) -> ToolExecutionResult:
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="grep",
        content="No matches found.",
        metadata={
            "mode": parsed.output_mode,
            "num_files": 0,
            "filtered_count": 0,
            "applied_limit": _effective_limit(parsed.head_limit, DEFAULT_HEAD_LIMIT),
            "applied_offset": parsed.offset,
            "truncated": False,
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


def _resolve_rg_path(path_text: str, cwd: Path) -> Path | None:
    if not path_text:
        return None
    return (cwd / path_text).resolve(strict=False)


def _extract_content_path(line: str) -> str | None:
    numbered_match = re.match(r"^(.+?)(?::\d+:|-\d+-)", line)
    if numbered_match is not None:
        return numbered_match.group(1)
    colon = line.find(":")
    if colon <= 0:
        return None
    return line[:colon]


def _replace_line_prefix(line: str, old_prefix: str, new_prefix: str) -> str:
    return f"{new_prefix}{line[len(old_prefix):]}"


def _paginate(items, parsed: GrepInput):
    limit = _effective_limit(parsed.head_limit, DEFAULT_HEAD_LIMIT)
    selected = items[parsed.offset :] if limit is None else items[
        parsed.offset : parsed.offset + limit
    ]
    truncated = parsed.offset + len(selected) < len(items)
    return selected, limit, truncated


def _append_pagination(lines: list[str], offset: int, limit: int | None, truncated: bool) -> None:
    if not truncated and not offset:
        return
    limit_label = "unlimited" if limit is None else str(limit)
    lines.append("")
    lines.append(
        f"[Showing results with pagination = offset: {offset}, limit: {limit_label}]"
    )


def _effective_limit(value: int | None, default: int) -> int | None:
    if value is None:
        return default
    if value == 0:
        return None
    return value


def _split_globs(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _display_path(path: Path, runtime: ToolRuntime) -> str:
    root = runtime.guard.boundary.cwd if runtime.guard is not None else Path.cwd()
    try:
        return path.resolve(strict=False).relative_to(root).as_posix()
    except ValueError:
        return path.resolve(strict=False).as_posix()


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _guard_error(policy) -> ToolExecutionResult:
    payload = policy.to_tool_error()
    if policy.action == "ask":
        payload["error"] = "path_guard_ask_required"
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="grep",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": payload["error"]},
    )


def _error_result(
    error: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> ToolExecutionResult:
    payload = {"error": error, "message": message}
    return ToolExecutionResult(
        tool_call_id="",
        tool_name="grep",
        content=json.dumps(payload, ensure_ascii=False),
        is_error=True,
        metadata={"error": error, **(metadata or {})},
    )


def _validation_message(exc: ValidationError) -> str:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    prefix = f"{location}: " if location else ""
    return f"{prefix}{first.get('msg', 'Tool input is invalid.')}"
