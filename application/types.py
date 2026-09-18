"""Application-layer contracts for the session controller.

These are the values the Interface exchanges: snapshots, ordered updates,
command/input receipts, interaction requests, and detail results. They carry
running facts and provider-neutral data, never Rich renderables or Textual
widgets. Field types are immutable values or copies so a subscriber cannot
mutate the runtime through a snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Union

from application.history import HistoryRecord
from services.model.types import ModelUsage
from services.tools.types import ToolExecutionResult

InteractionKind = Literal["permission", "question", "mcp_trust", "plan_approval"]
InputKind = Literal["prompt", "command"]
SubmissionStatus = Literal["started", "queued", "rejected"]
ToolStatus = Literal["declared", "started", "progress", "completed", "error"]


@dataclass(frozen=True)
class QueueItem:
    """One accepted-but-not-yet-run input. Not a committed user message."""

    input_id: str
    text: str
    kind: InputKind = "prompt"
    attachments: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "input_id": self.input_id,
            "text": self.text,
            "kind": self.kind,
            "attachments": [dict(item) for item in self.attachments],
        }


@dataclass(frozen=True)
class ToolRunState:
    tool_call_id: str
    tool_name: str
    status: ToolStatus = "declared"
    text: str = ""
    is_error: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunState:
    """The current foreground turn as the runtime really has it."""

    active: bool = False
    input_id: str | None = None
    text: str = ""
    assistant_text: str = ""
    assistant_call_id: str | None = None
    model_turn_index: int | None = None
    tools: tuple[ToolRunState, ...] = ()
    status: str = "idle"
    run_id: str | None = None


@dataclass(frozen=True)
class InteractionRequest:
    request_id: str
    session_id: str
    kind: InteractionKind
    payload: Any
    run_id: str | None = None
    options: tuple[Any, ...] = ()


@dataclass(frozen=True)
class InteractionAnswer:
    request_id: str
    kind: InteractionKind
    payload: Any = None
    cancelled: bool = False


@dataclass(frozen=True)
class SessionSnapshot:
    session_id: str
    generation: int
    sequence: int
    initialized: bool
    configured: bool
    paused: bool
    status: str
    run: RunState
    queue: tuple[QueueItem, ...]
    history: tuple[HistoryRecord, ...]
    interaction: InteractionRequest | None
    pending_interactions: tuple[InteractionRequest, ...] = ()
    provider_label: str = ""
    model: str = ""
    usage: ModelUsage = field(default_factory=ModelUsage)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class SubmissionReceipt:
    input_id: str
    session_id: str
    status: SubmissionStatus
    reason: str = ""
    queue_length: int = 0


@dataclass(frozen=True)
class WithdrawalResult:
    input_id: str
    withdrawn: bool
    text: str = ""
    reason: str = ""


@dataclass(frozen=True)
class CancelResult:
    cancelled: bool
    reason: str = ""
    cleanup_success: bool = True


@dataclass(frozen=True)
class ResponseResult:
    request_id: str
    accepted: bool
    reason: str = ""


@dataclass(frozen=True)
class DetailRef:
    session_id: str
    kind: Literal["tool_result", "message"]
    identifier: str
    relative_path: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "kind": self.kind,
            "identifier": self.identifier,
            "relative_path": self.relative_path,
        }


@dataclass(frozen=True)
class DetailResult:
    success: bool
    text: str = ""
    error: str | None = None
    missing: bool = False
    externalized: bool = False


# --- Ordered updates -------------------------------------------------------


@dataclass(frozen=True)
class SnapshotUpdate:
    snapshot: SessionSnapshot


@dataclass(frozen=True)
class RunStarted:
    generation: int
    sequence: int
    input_id: str
    text: str


@dataclass(frozen=True)
class AssistantDelta:
    generation: int
    sequence: int
    text: str
    assistant_call_id: str | None = None
    model_turn_index: int | None = None


@dataclass(frozen=True)
class MessageCommitted:
    generation: int
    sequence: int
    message_uuid: str
    text: str = ""
    assistant_call_id: str | None = None
    model_turn_index: int | None = None


@dataclass(frozen=True)
class ToolUpdate:
    generation: int
    sequence: int
    tool_call_id: str
    tool_name: str
    status: ToolStatus
    text: str = ""
    is_error: bool = False
    result: ToolExecutionResult | None = None


@dataclass(frozen=True)
class RunCompleted:
    generation: int
    sequence: int
    input_id: str
    text: str


@dataclass(frozen=True)
class RunFailed:
    generation: int
    sequence: int
    input_id: str
    error: str
    recoverable: bool = True


@dataclass(frozen=True)
class RunCancelled:
    generation: int
    sequence: int
    input_id: str
    cleanup_success: bool = True
    error: str | None = None


@dataclass(frozen=True)
class QueueChanged:
    generation: int
    sequence: int
    queue: tuple[QueueItem, ...]
    paused: bool


@dataclass(frozen=True)
class StatusChanged:
    generation: int
    sequence: int
    status: str
    configured: bool | None = None


@dataclass(frozen=True)
class InteractionRequested:
    generation: int
    sequence: int
    request: InteractionRequest


@dataclass(frozen=True)
class InteractionResolved:
    generation: int
    sequence: int
    request_id: str
    outcome: str


@dataclass(frozen=True)
class DetailLoaded:
    generation: int
    sequence: int
    ref: DetailRef
    result: DetailResult


SessionUpdate = Union[
    SnapshotUpdate,
    RunStarted,
    AssistantDelta,
    MessageCommitted,
    ToolUpdate,
    RunCompleted,
    RunFailed,
    RunCancelled,
    QueueChanged,
    StatusChanged,
    InteractionRequested,
    InteractionResolved,
    DetailLoaded,
]


__all__ = [
    "AssistantDelta",
    "CancelResult",
    "DetailLoaded",
    "DetailRef",
    "DetailResult",
    "InputKind",
    "InteractionAnswer",
    "InteractionKind",
    "InteractionRequest",
    "InteractionRequested",
    "InteractionResolved",
    "MessageCommitted",
    "QueueChanged",
    "QueueItem",
    "ResponseResult",
    "RunCancelled",
    "RunCompleted",
    "RunFailed",
    "RunStarted",
    "RunState",
    "SessionSnapshot",
    "SessionUpdate",
    "SnapshotUpdate",
    "StatusChanged",
    "SubmissionReceipt",
    "ToolRunState",
    "ToolUpdate",
    "WithdrawalResult",
]
