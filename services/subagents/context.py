"""Current parent model-call context exposed to subagent tooling."""

from __future__ import annotations

from dataclasses import dataclass

from services.context.snapshot import ContextSnapshot


@dataclass
class CurrentModelContext:
    snapshot: ContextSnapshot | None = None
