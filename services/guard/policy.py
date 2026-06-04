"""Guard service entry points and structured policy results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from services.guard.boundary import (
    Operation,
    SandboxBoundary,
    SandboxDecision,
    TargetKind,
    classify_path,
)


GuardAction = Literal["allow", "ask", "deny"]


@dataclass(frozen=True)
class GuardPolicy:
    action: GuardAction
    decision: SandboxDecision
    operation: Operation
    target_kind: TargetKind
    original_path: str
    normalized_path: Path
    reason: str
    pattern: str | None = None

    def to_tool_error(self) -> dict[str, object]:
        """Return a structured tool result payload for blocked paths."""

        return {
            "error": "path_guard_denied",
            "original_path": self.original_path,
            "normalized_path": str(self.normalized_path),
            "operation": self.operation,
            "kind": self.target_kind,
            "decision": self.decision.kind,
            "pattern": self.pattern,
            "reason": self.reason,
        }


class SandboxGuard:
    """Unified sandbox guard for filesystem tools."""

    def __init__(self, boundary: SandboxBoundary) -> None:
        self.boundary = boundary

    def check_path(
        self,
        target: str | Path,
        *,
        operation: Operation = "read",
        kind: TargetKind = "file",
    ) -> GuardPolicy:
        decision = classify_path(
            self.boundary,
            target,
            operation=operation,
            kind=kind,
        )
        original_path = str(target)
        # GuardPolicy 同时保留模型给出的原始路径和规范化路径，
        # 让工具错误既可读又不丢审计细节。
        if decision.kind == "denied":
            return GuardPolicy(
                action="deny",
                decision=decision,
                operation=operation,
                target_kind=kind,
                original_path=original_path,
                normalized_path=decision.path,
                reason=decision.reason,
                pattern=decision.pattern,
            )
        if decision.kind == "external_directory":
            return GuardPolicy(
                action="ask",
                decision=decision,
                operation=operation,
                target_kind=kind,
                original_path=original_path,
                normalized_path=decision.path,
                reason="Path is outside the configured sandbox boundary.",
                pattern=decision.pattern,
            )
        return GuardPolicy(
            action="allow",
            decision=decision,
            operation=operation,
            target_kind=kind,
            original_path=original_path,
            normalized_path=decision.path,
            reason="Path is inside the configured sandbox boundary.",
        )

    def check_write_target(
        self,
        target: str | Path,
        *,
        kind: TargetKind = "file",
    ) -> GuardPolicy:
        return self.check_path(target, operation="write", kind=kind)
