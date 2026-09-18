"""长期记忆目录的兼容性扫描辅助函数。"""

from __future__ import annotations

from pathlib import Path

from services.memory.auto_store import LongTermMemoryStore
from services.memory.types import LongTermMemoryFile


def scan_memory_files(workspace: Path | str) -> tuple[LongTermMemoryFile, ...]:
    return LongTermMemoryStore(workspace).scan()
