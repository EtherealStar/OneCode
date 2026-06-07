"""Provider-neutral permission request and decision types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from services.tools.types import ToolTarget

if TYPE_CHECKING:
    from services.permissions.rules import PermissionUpdate
    from services.guard import GuardPolicy
    from services.tools.types import ToolCall, ToolCallClassification, ToolDescriptor


PermissionAction = Literal["allow", "ask", "deny", "passthrough"]
PermissionScope = Literal["once", "session", "project"]


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    reason: str
    source: str
    targets: tuple[ToolTarget, ...] = ()
    guard_policies: tuple[GuardPolicy, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PermissionOption:
    id: str
    label: str
    action: Literal["allow", "deny"]
    scope: PermissionScope = "once"
    description: str = ""


@dataclass(frozen=True)
class PermissionRequest:
    request_id: str
    tool_call: ToolCall
    descriptor: ToolDescriptor
    classification: ToolCallClassification
    decision: PermissionDecision
    tool_input: dict[str, Any]
    options: tuple[PermissionOption, ...] = ()


@dataclass(frozen=True)
class PermissionResponse:
    action: Literal["allow", "deny"]
    scope: PermissionScope = "once"
    feedback: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    permission_updates: tuple[PermissionUpdate, ...] = ()
