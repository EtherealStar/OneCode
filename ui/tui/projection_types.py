"""Presentation value types for the conversation projection.

They carry only what the view needs to render: stable identities, ordered
parts, tool execution state, result previews, and detail references. They never
expose provider wire formats, Rich renderables, or Textual widgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from application.types import DetailRef

MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"
MESSAGE_ROLE_SYSTEM = "system"

LIFECYCLE_DRAFT = "draft"
LIFECYCLE_COMPLETED = "completed"
LIFECYCLE_FAILED = "failed"

PART_TEXT = "text"
PART_TOOL = "tool"
PART_ATTACHMENT = "attachment"


@dataclass(frozen=True)
class UiPart:
    """One ordered fragment of a message."""

    kind: str
    #: Text body (``text``) or attachment summary (``attachment``).
    content: str = ""
    #: Attachment metadata, present for ``attachment`` parts.
    attachment_type: str | None = None
    #: Tool identity, present for ``tool`` parts.
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: Mapping[str, Any] = field(default_factory=dict)
    status: str | None = None
    is_error: bool = False
    progress_text: str = ""
    result_preview: str | None = None
    detail_ref: DetailRef | None = None
    detail_loaded: bool = False
    detail_error: str | None = None


@dataclass(frozen=True)
class UiMessage:
    """A stable message identity with ordered parts and a lifecycle."""

    message_id: str
    role: str
    parts: tuple[UiPart, ...] = ()
    lifecycle: str = LIFECYCLE_COMPLETED


@dataclass(frozen=True)
class ViewChange:
    """Semantic change set handed to the view.

    It contains no heights, ANSI, scroll offsets, or timers: the view owns how
    to coalesce refreshes. ``resync_required`` tells the caller a sequence gap
    was observed and it must fetch a complete snapshot instead of guessing.
    """

    updated_ids: tuple[str, ...] = ()
    deleted_ids: tuple[str, ...] = ()
    structure_changed: bool = False
    queue_changed: bool = False
    status_changed: bool = False
    interaction_changed: bool = False
    usage_changed: bool = False
    run_changed: bool = False
    reset: bool = False
    resync_required: bool = False
    immediate: bool = False

    @property
    def empty(self) -> bool:
        return not (
            self.updated_ids
            or self.deleted_ids
            or self.structure_changed
            or self.queue_changed
            or self.status_changed
            or self.interaction_changed
            or self.usage_changed
            or self.run_changed
            or self.reset
            or self.resync_required
        )

    @classmethod
    def none(cls) -> "ViewChange":
        return cls()


__all__ = [
    "LIFECYCLE_COMPLETED",
    "LIFECYCLE_DRAFT",
    "LIFECYCLE_FAILED",
    "MESSAGE_ROLE_ASSISTANT",
    "MESSAGE_ROLE_SYSTEM",
    "MESSAGE_ROLE_USER",
    "PART_ATTACHMENT",
    "PART_TEXT",
    "PART_TOOL",
    "UiMessage",
    "UiPart",
    "ViewChange",
]
