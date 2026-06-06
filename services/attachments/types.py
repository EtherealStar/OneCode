"""Stable attachment message types."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
import uuid


class AttachmentScope(StrEnum):
    SHARED = "shared"
    MAIN_THREAD = "main_thread"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AttachmentMessage:
    attachment: dict[str, Any]
    attachment_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_timestamp)
    scope: AttachmentScope = AttachmentScope.MAIN_THREAD
    source: str = "user_input"

    def to_message(self) -> dict[str, Any]:
        """Wrap one attachment as a durable internal message."""

        attachment = deepcopy(self.attachment)
        attachment.setdefault("id", self.attachment_id)
        attachment.setdefault("created_at", self.created_at)
        metadata = {
            "attachment_id": self.attachment_id,
            "attachment_type": attachment.get("type", "unknown"),
            "scope": self.scope.value,
            "source": self.source,
        }
        return {
            "role": "attachment",
            "content": "",
            "attachment": attachment,
            "metadata": metadata,
        }
