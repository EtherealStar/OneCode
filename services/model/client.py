"""Model client protocol."""

from __future__ import annotations

from typing import Protocol

from services.context.snapshot import ContextSnapshot
from services.model.types import LLMResponse


class ModelClient(Protocol):
    def send(self, snapshot: ContextSnapshot) -> LLMResponse:
        ...
