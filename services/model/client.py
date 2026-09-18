"""模型客户端协议。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from services.context.snapshot import ContextSnapshot
from services.model.stream import ModelStreamEvent


class ModelClient(Protocol):
    def stream(self, snapshot: ContextSnapshot) -> AsyncIterator[ModelStreamEvent]:
        ...
