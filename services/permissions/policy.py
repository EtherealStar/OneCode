"""Permission policy that coordinates guard decisions and session grants."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from core.runtime_state import RuntimeState
from infrastructure.filesystem.paths import resolve_path
from services.guard import GuardPolicy
from services.permissions.session import SessionPermissionStore
from services.permissions.types import (
    PermissionDecision,
    PermissionOption,
    PermissionRequest,
    PermissionResponse,
)
from services.tools.types import ToolCall, ToolCallClassification, ToolDescriptor

PROTECTED_PROJECT_DIRS = (".git", ".vscode", ".idea", ".onecode")
_WINDOWS_FORM_RE = re.compile(
    r"^(?:[a-zA-Z]:[\\/]|/[a-zA-Z](?:/|$)|/[a-zA-Z]:(?:/|$)|"
    r"/mnt/[a-zA-Z](?:/|$)|/cygdrive/[a-zA-Z](?:/|$)|\\\\)"
)
_RESERVED_DEVICE_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class PermissionPolicy:
    """Deny-first policy for concrete tool calls.

    The policy is intentionally conservative: session grants can only turn an
    ``ask`` into ``allow`` and never override guard or tool-level deny results.
    """

    def __init__(
        self,
        session_store: SessionPermissionStore | None = None,
        *,
        protected_project_dirs: tuple[str, ...] = PROTECTED_PROJECT_DIRS,
    ) -> None:
        self.session_store = session_store or SessionPermissionStore()
        self.protected_project_dirs = tuple(protected_project_dirs)

    def evaluate(
        self,
        *,
        tool_call: ToolCall,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
        state: RuntimeState,
    ) -> PermissionDecision:
        if state.metadata.get("read_only_agent") is True and (
            not classification.read_only or classification.modifies_filesystem
        ):
            return PermissionDecision(
                action="deny",
                reason="Read-only subagent cannot execute state-changing tool calls.",
                source="read_only_agent",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        if self.is_tool_denied(descriptor.name, state):
            return PermissionDecision(
                action="deny",
                reason=f"Tool is denied: {descriptor.name}",
                source="tool_policy",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        if self.is_tool_disabled(descriptor.name, state):
            return PermissionDecision(
                action="deny",
                reason=f"Tool is disabled: {descriptor.name}",
                source="tool_policy",
                targets=classification.targets,
                guard_policies=guard_policies,
            )

        for policy in guard_policies:
            if policy.action == "deny":
                return PermissionDecision(
                    action="deny",
                    reason=policy.reason,
                    source="guard",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                    metadata={"guard_policy": policy.to_tool_error()},
                )

        asks = self._ask_reasons(
            descriptor=descriptor,
            classification=classification,
            guard_policies=guard_policies,
        )
        if asks:
            if self._session_allows_all(
                descriptor=descriptor,
                guard_policies=guard_policies,
            ):
                return PermissionDecision(
                    action="allow",
                    reason="Allowed by a session permission grant.",
                    source="session",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                    metadata={"ask_reasons": asks},
                )
            return PermissionDecision(
                action="ask",
                reason="; ".join(asks),
                source="permission_policy",
                targets=classification.targets,
                guard_policies=guard_policies,
                metadata={"ask_reasons": asks},
            )

        return PermissionDecision(
            action="allow",
            reason="Permission policy allowed the tool call.",
            source="permission_policy",
            targets=classification.targets,
            guard_policies=guard_policies,
        )

    def request_for_decision(
        self,
        *,
        tool_call: ToolCall,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        decision: PermissionDecision,
        tool_input: dict[str, Any],
    ) -> PermissionRequest:
        return PermissionRequest(
            request_id=f"perm-{tool_call.id}",
            tool_call=tool_call,
            descriptor=descriptor,
            classification=classification,
            decision=decision,
            tool_input=dict(tool_input),
            options=(
                PermissionOption(
                    id="allow_once",
                    label="allow once",
                    action="allow",
                    scope="once",
                ),
                PermissionOption(
                    id="allow_session_directory",
                    label="allow this directory for this session",
                    action="allow",
                    scope="session",
                ),
                PermissionOption(
                    id="deny",
                    label="deny",
                    action="deny",
                    scope="once",
                ),
            ),
        )

    def record_response(
        self,
        request: PermissionRequest,
        response: PermissionResponse,
    ) -> None:
        if response.action != "allow" or response.scope != "session":
            return
        for policy in request.decision.guard_policies:
            if policy.action == "deny":
                continue
            self.session_store.allow_directory(
                tool_name=request.descriptor.name,
                operation=policy.operation,
                directory=_grant_directory(policy),
            )

    def is_tool_denied(self, tool_name: str, state: RuntimeState) -> bool:
        return self.session_store.is_tool_denied(tool_name) or tool_name in _names(
            state.metadata.get("denied_tools")
        )

    def is_tool_disabled(self, tool_name: str, state: RuntimeState) -> bool:
        return self.session_store.is_tool_disabled(tool_name) or tool_name in _names(
            state.metadata.get("disabled_tools")
        )

    def is_tool_visible(self, descriptor: ToolDescriptor, state: RuntimeState) -> bool:
        return not (
            self.is_tool_denied(descriptor.name, state)
            or self.is_tool_disabled(descriptor.name, state)
        )

    def _ask_reasons(
        self,
        *,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
    ) -> list[str]:
        reasons: list[str] = []
        for target in classification.targets:
            if (
                target.kind == "command"
                and target.operation == "execute"
                and not classification.read_only
            ):
                reasons.append(
                    "Command may modify system state or has unknown side effects."
                )
        for policy in guard_policies:
            protected = _protected_project_dir(
                policy.normalized_path,
                self.protected_project_dirs,
            )
            if protected is not None:
                reasons.append(
                    f"Target is inside a protected project directory: {protected}"
                )
            if policy.action != "allow" and _is_suspicious_windows_path(
                policy.original_path, policy.normalized_path
            ):
                reasons.append("Target uses a suspicious Windows path form.")
            if policy.action == "ask":
                reasons.append(policy.reason)
        return _dedupe(reasons)

    def _session_allows_all(
        self,
        *,
        descriptor: ToolDescriptor,
        guard_policies: tuple[GuardPolicy, ...],
    ) -> bool:
        ask_policies = [
            policy
            for policy in guard_policies
            if policy.action == "ask"
            or _protected_project_dir(
                policy.normalized_path,
                self.protected_project_dirs,
            )
            is not None
            or (
                policy.action != "allow"
                and _is_suspicious_windows_path(
                    policy.original_path, policy.normalized_path
                )
            )
        ]
        if not ask_policies:
            return False
        return all(
            self.session_store.is_allowed(
                tool_name=descriptor.name,
                operation=policy.operation,
                target=policy.normalized_path,
            )
            for policy in ask_policies
        )


def _grant_directory(policy: GuardPolicy) -> Path:
    if policy.target_kind == "directory":
        return policy.normalized_path
    return policy.normalized_path.parent


def _protected_project_dir(
    path: Path,
    protected_dirs: tuple[str, ...],
) -> str | None:
    protected = {name.lower() for name in protected_dirs}
    for part in resolve_path(path).parts:
        if part.lower() in protected:
            return part
    return None


def _is_suspicious_windows_path(original_path: str, normalized_path: Path) -> bool:
    raw = original_path.replace("\\", "/")
    if _WINDOWS_FORM_RE.match(raw):
        return True
    for part in Path(raw).parts:
        stem = part.split(".", 1)[0].upper()
        if stem in _RESERVED_DEVICE_NAMES:
            return True
    return False


def _names(value: object) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    try:
        return {str(item) for item in value if str(item)}
    except TypeError:
        return {str(value)} if str(value) else set()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
