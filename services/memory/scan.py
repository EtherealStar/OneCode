"""Compatibility scan helpers for long-term memory catalogs."""

from __future__ import annotations

from pathlib import Path

from services.memory.auto_store import LongTermMemoryStore
from services.memory.types import LongTermMemoryFile


def scan_memory_files(workspace: Path | str) -> tuple[LongTermMemoryFile, ...]:
    return LongTermMemoryStore(workspace).scan()
