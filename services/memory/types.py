"""Types for OneCode long-term memory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MemoryKind = Literal["user", "feedback", "project", "reference"]


@dataclass(frozen=True)
class MemoryPaths:
    workspace: Path
    memory_dir: Path
    entrypoint: Path


@dataclass(frozen=True)
class InstructionMemoryFile:
    path: Path
    source_layer: str
    content: str
    globs: tuple[str, ...] = ()
    parent: Path | None = None
    transformed: bool = False
    load_reason: str = ""


@dataclass(frozen=True)
class InstructionMemoryResult:
    files: tuple[InstructionMemoryFile, ...]
    rendered_text: str
    fingerprint: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class LongTermMemoryFile:
    path: Path
    relative_path: str
    name: str
    description: str
    type: MemoryKind
    mtime: float
    preview: str = ""
