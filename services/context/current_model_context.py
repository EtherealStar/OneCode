"""Current model-call snapshot holder shared by runtime services."""

from __future__ import annotations

from dataclasses import dataclass

from services.context.snapshot import ContextSnapshot


@dataclass
class CurrentModelContext:
    snapshot: ContextSnapshot | None = None
