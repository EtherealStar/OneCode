"""Workspace-local long-term memory store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services.memory.frontmatter import clean_string, split_frontmatter
from services.memory.paths import is_auto_memory_path, memory_paths, normalize_memory_path
from services.memory.types import LongTermMemoryFile, MemoryKind

VALID_TYPES: tuple[MemoryKind, ...] = ("user", "feedback", "project", "reference")


class LongTermMemoryStore:
    def __init__(self, workspace: Path | str) -> None:
        paths = memory_paths(workspace)
        self.workspace = paths.workspace
        self.memory_dir = paths.memory_dir
        self.entrypoint_path = paths.entrypoint

    def ensure_exists(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        if not self.entrypoint_path.exists():
            self.entrypoint_path.write_text("", encoding="utf-8")

    def read_entrypoint(self) -> str:
        try:
            return self.entrypoint_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""

    def truncated_entrypoint(
        self,
        *,
        max_lines: int = 200,
        max_chars: int = 25_000,
    ) -> tuple[str, bool]:
        text = self.read_entrypoint()
        lines = text.splitlines()
        truncated = False
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            truncated = True
        rendered = "\n".join(lines)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip()
            truncated = True
        if truncated:
            rendered += "\n\n[Long-term memory index truncated. Move details into topic files.]"
        return rendered, truncated

    def scan(self) -> tuple[LongTermMemoryFile, ...]:
        if not self.memory_dir.exists():
            return ()
        files: list[LongTermMemoryFile] = []
        for path in sorted(self.memory_dir.rglob("*.md")):
            if path.resolve() == self.entrypoint_path.resolve():
                continue
            parsed = self._parse_topic(path)
            if parsed is not None:
                files.append(parsed)
        files.sort(key=lambda item: item.mtime, reverse=True)
        return tuple(files)

    def read_topic(
        self,
        relative_path: str | Path,
        *,
        max_lines: int = 200,
        max_chars: int = 4096,
    ) -> str:
        path = normalize_memory_path(relative_path, self.workspace)
        if not is_auto_memory_path(path, self.workspace):
            raise ValueError(f"Memory topic is outside memory dir: {relative_path}")
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()[:max_lines]
        rendered = "\n".join(lines)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars].rstrip() + "\n[Memory topic truncated]"
        return rendered

    def rebuild_entrypoint(self) -> None:
        self.ensure_exists()
        lines = [
            f"- [{memory.name}]({memory.relative_path}) - {memory.description}"
            for memory in sorted(self.scan(), key=lambda item: item.relative_path)
        ]
        self.entrypoint_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    def record_memory_write(self, state: Any, path: Path | str) -> None:
        writes = state.metadata.setdefault("long_term_memory_writes", [])
        if not isinstance(writes, list):
            writes = list(writes)
            state.metadata["long_term_memory_writes"] = writes
        writes.append(
            {
                "turn_count": state.turn_count,
                "message_count": state.metadata.get("message_count"),
                "path": str(Path(path).resolve()),
            }
        )

    def _parse_topic(self, path: Path) -> LongTermMemoryFile | None:
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        metadata, body = split_frontmatter(raw)
        name = clean_string(metadata.get("name")) or path.stem
        description = clean_string(metadata.get("description")) or _first_body_line(body) or name
        memory_type = clean_string(metadata.get("type")) or "project"
        if memory_type not in VALID_TYPES:
            memory_type = "project"
        try:
            relative_path = path.relative_to(self.memory_dir).as_posix()
        except ValueError:
            return None
        return LongTermMemoryFile(
            path=path.resolve(),
            relative_path=relative_path,
            name=name,
            description=description,
            type=memory_type,  # type: ignore[arg-type]
            mtime=path.stat().st_mtime,
            preview=_preview(body),
        )


def _first_body_line(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:160]
    return ""


def _preview(body: str, limit: int = 500) -> str:
    text = " ".join(body.split())
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."
