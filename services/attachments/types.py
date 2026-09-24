"""稳定的附件消息类型定义。"""

from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class AttachmentScope(StrEnum):
    SHARED = "shared"
    MAIN_THREAD = "main_thread"


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class AttachmentMessage:
    attachment: dict[str, Any]
    attachment_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_timestamp)
    scope: AttachmentScope = AttachmentScope.MAIN_THREAD
    source: str = "user_input"

    def to_message(self) -> dict[str, Any]:
        """将单个附件包装为持久化的内部消息。"""

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
