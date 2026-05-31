from __future__ import annotations

import glob as globlib
import subprocess
from pathlib import Path

from .base import Tool, ToolMeta
from .registry import ToolRegistry


def safe_path(cwd: Path, path: str) -> Path:
    resolved = (cwd / path).resolve()
    if not resolved.is_relative_to(cwd.resolve()):
        raise ValueError(f"Path escapes workspace: {path}")
    return resolved


def _truncate(content: str, max_chars: int | None) -> str:
    if max_chars is None or len(content) <= max_chars:
        return content
    return content[:max_chars] + f"\n[truncated {len(content) - max_chars} chars]"


def build_builtin_registry(cwd: Path) -> ToolRegistry:
    cwd = cwd.resolve()

    def run_bash(command: str) -> str:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip()
        if not output:
            output = "(no output)"
        if result.returncode != 0:
            output = f"[exit {result.returncode}]\n{output}"
        return _truncate(output, 50_000)

    def read_file(path: str, limit: int | None = None) -> str:
        file_path = safe_path(cwd, path)
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if limit is not None and limit >= 0 and len(lines) > limit:
            hidden = len(lines) - limit
            lines = lines[:limit] + [f"... ({hidden} more lines)"]
        return "\n".join(lines)

    def write_file(path: str, content: str) -> str:
        file_path = safe_path(cwd, path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"

    def edit_file(path: str, old_text: str, new_text: str) -> str:
        file_path = safe_path(cwd, path)
        text = file_path.read_text(encoding="utf-8", errors="replace")
        if old_text not in text:
            raise ValueError(f"Text not found in {path}")
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"

    def glob(pattern: str) -> str:
        matches: list[str] = []
        for match in globlib.glob(pattern, root_dir=cwd, recursive=True):
            resolved = (cwd / match).resolve()
            if resolved.is_relative_to(cwd):
                matches.append(str(Path(match)))
        return "\n".join(sorted(matches)) if matches else "(no matches)"

    return ToolRegistry(
        [
            Tool(
                ToolMeta(
                    name="bash",
                    description="Run a shell command in the workspace.",
                    input_schema={
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                    read_only=False,
                    concurrency_safe=False,
                    requires_permission=True,
                ),
                run_bash,
            ),
            Tool(
                ToolMeta(
                    name="read_file",
                    description="Read a UTF-8 text file inside the workspace.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "limit": {"type": "integer"},
                        },
                        "required": ["path"],
                    },
                    read_only=True,
                    concurrency_safe=True,
                    max_result_chars=None,
                ),
                read_file,
            ),
            Tool(
                ToolMeta(
                    name="write_file",
                    description="Write a UTF-8 text file inside the workspace.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                    read_only=False,
                    concurrency_safe=False,
                    mutates_filesystem=True,
                    requires_permission=True,
                ),
                write_file,
            ),
            Tool(
                ToolMeta(
                    name="edit_file",
                    description="Replace exact text in a file once.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_text": {"type": "string"},
                            "new_text": {"type": "string"},
                        },
                        "required": ["path", "old_text", "new_text"],
                    },
                    read_only=False,
                    concurrency_safe=False,
                    mutates_filesystem=True,
                    requires_permission=True,
                ),
                edit_file,
            ),
            Tool(
                ToolMeta(
                    name="glob",
                    description="Find files in the workspace by glob pattern.",
                    input_schema={
                        "type": "object",
                        "properties": {"pattern": {"type": "string"}},
                        "required": ["pattern"],
                    },
                    read_only=True,
                    concurrency_safe=True,
                ),
                glob,
            ),
        ]
    )
