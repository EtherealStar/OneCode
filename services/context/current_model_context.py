"""运行时服务共享的当前模型调用快照持有者。"""

from __future__ import annotations

from dataclasses import dataclass

from services.context.snapshot import ContextSnapshot


@dataclass
class CurrentModelContext:
    snapshot: ContextSnapshot | None = None
