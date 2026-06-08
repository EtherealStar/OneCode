"""Permission policy that coordinates guard decisions and session grants."""

from __future__ import annotations

from pathlib import Path
import fnmatch
import re
from typing import Any

from core.runtime_state import RuntimeState
from infrastructure.filesystem.paths import resolve_path
from services.guard import GuardPolicy
from services.memory.paths import is_auto_memory_markdown_path, is_auto_memory_path
from services.permissions.project_settings import ProjectPermissionSettingsStore
from services.permissions.rules import PermissionBehavior, PermissionRule
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
        project_store: ProjectPermissionSettingsStore | None = None,
        protected_project_dirs: tuple[str, ...] = PROTECTED_PROJECT_DIRS,
        scoped_allowed_tools: tuple[str, ...] = (),
    ) -> None:
        self.session_store = session_store or SessionPermissionStore()
        self.project_store = project_store
        self.protected_project_dirs = tuple(protected_project_dirs)
        self._scoped_allowed_tools = _names(scoped_allowed_tools)

    def with_scoped_allowed_tools(
        self,
        allowed_tools: tuple[str, ...],
    ) -> PermissionPolicy:
        """Return a policy sharing persistent grants plus one runtime-local grant set."""

        return PermissionPolicy(
            self.session_store,
            project_store=self.project_store,
            protected_project_dirs=self.protected_project_dirs,
            scoped_allowed_tools=tuple(
                sorted(self._scoped_allowed_tools | _names(allowed_tools))
            ),
        )

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
        denied_skill = self._denied_skill_name(classification, state)
        if descriptor.name == "skill" and denied_skill is not None:
            return PermissionDecision(
                action="deny",
                reason=f"Skill is denied: {denied_skill}",
                source="skill_policy",
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

        project_deny = self._matching_project_rules(
            "deny",
            descriptor=descriptor,
            classification=classification,
            guard_policies=guard_policies,
        )
        if project_deny:
            return PermissionDecision(
                action="deny",
                reason=_project_rule_reason("deny", project_deny),
                source="project_settings",
                targets=classification.targets,
                guard_policies=guard_policies,
                metadata={"project_rules": _rule_strings(project_deny)},
            )

        if state.metadata.get("memory_extraction_agent") is True:
            return self._memory_extraction_decision(
                descriptor=descriptor,
                classification=classification,
                guard_policies=guard_policies,
                state=state,
            )
        if state.metadata.get("long_term_memory_extraction_agent") is True:
            return self._long_term_memory_extraction_decision(
                descriptor=descriptor,
                classification=classification,
                guard_policies=guard_policies,
                state=state,
            )

        asks = self._ask_reasons(
            descriptor=descriptor,
            classification=classification,
            guard_policies=guard_policies,
            state=state,
        )
        if asks:
            if self._project_allows_call(
                descriptor=descriptor,
                classification=classification,
                guard_policies=guard_policies,
            ):
                return PermissionDecision(
                    action="allow",
                    reason="Allowed by project permission settings.",
                    source="project_settings",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                    metadata={"ask_reasons": asks},
                )
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
            if self.session_store.is_tool_allowed(descriptor.name):
                return PermissionDecision(
                    action="allow",
                    reason="Allowed by a session tool grant.",
                    source="session",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                    metadata={"ask_reasons": asks},
                )
            if descriptor.name in self._scoped_allowed_tools:
                return PermissionDecision(
                    action="allow",
                    reason="Allowed by a scoped tool grant.",
                    source="scoped_tool_grant",
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

    def _memory_extraction_decision(
        self,
        *,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
        state: RuntimeState,
    ) -> PermissionDecision:
        """Hard-limit internal memory agents to one Markdown write target."""

        allowed_path = state.metadata.get("allowed_memory_path")
        if not isinstance(allowed_path, str) or not allowed_path:
            return PermissionDecision(
                action="deny",
                reason="Memory extraction agent has no allowed memory path.",
                source="memory_extraction_agent",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        if descriptor.name != "edit_file":
            return PermissionDecision(
                action="deny",
                reason="Memory extraction agent can only use edit_file.",
                source="memory_extraction_agent",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        normalized_allowed = resolve_path(Path(allowed_path))
        targets = classification.targets
        if len(targets) != 1:
            return PermissionDecision(
                action="deny",
                reason="Memory extraction edit must target exactly one file.",
                source="memory_extraction_agent",
                targets=targets,
                guard_policies=guard_policies,
            )
        target = targets[0]
        if target.kind != "file" or target.operation != "write":
            return PermissionDecision(
                action="deny",
                reason="Memory extraction edit must be a file write.",
                source="memory_extraction_agent",
                targets=targets,
                guard_policies=guard_policies,
            )
        target_path = resolve_path(Path(target.value))
        if target_path != normalized_allowed:
            return PermissionDecision(
                action="deny",
                reason="Memory extraction agent cannot edit outside session memory.",
                source="memory_extraction_agent",
                targets=targets,
                guard_policies=guard_policies,
            )
        return PermissionDecision(
            action="allow",
            reason="Memory extraction agent may edit the session memory file.",
            source="memory_extraction_agent",
            targets=targets,
            guard_policies=guard_policies,
        )

    def _long_term_memory_extraction_decision(
        self,
        *,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
        state: RuntimeState,
    ) -> PermissionDecision:
        """Hard-limit internal long-term memory agents."""

        allowed_dir = state.metadata.get("allowed_memory_dir")
        if not isinstance(allowed_dir, str) or not allowed_dir:
            return PermissionDecision(
                action="deny",
                reason="Long-term memory extraction agent has no allowed memory directory.",
                source="long_term_memory_extraction_agent",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        workspace = _workspace_from_memory_dir(Path(allowed_dir))
        allowed_read_tools = {"read_file", "grep", "glob"}
        allowed_write_tools = {"write_file", "edit_file"}
        if descriptor.name not in allowed_read_tools | allowed_write_tools:
            return PermissionDecision(
                action="deny",
                reason="Long-term memory extraction agent can only use file search/read and memory write tools.",
                source="long_term_memory_extraction_agent",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        if descriptor.name in allowed_read_tools:
            if not classification.read_only or classification.modifies_filesystem:
                return PermissionDecision(
                    action="deny",
                    reason="Long-term memory extraction read tools must be read-only.",
                    source="long_term_memory_extraction_agent",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                )
            if any(policy.action != "allow" for policy in guard_policies):
                return PermissionDecision(
                    action="deny",
                    reason="Long-term memory extraction agent cannot read outside the workspace boundary.",
                    source="long_term_memory_extraction_agent",
                    targets=classification.targets,
                    guard_policies=guard_policies,
                )
            return PermissionDecision(
                action="allow",
                reason="Long-term memory extraction agent may read workspace context.",
                source="long_term_memory_extraction_agent",
                targets=classification.targets,
                guard_policies=guard_policies,
            )
        targets = classification.targets
        if not targets:
            return PermissionDecision(
                action="deny",
                reason="Long-term memory writes must target a memory Markdown file.",
                source="long_term_memory_extraction_agent",
                targets=targets,
                guard_policies=guard_policies,
            )
        for index, target in enumerate(targets):
            if target.kind != "file" or target.operation != "write":
                return PermissionDecision(
                    action="deny",
                    reason="Long-term memory extraction writes must be file writes.",
                    source="long_term_memory_extraction_agent",
                    targets=targets,
                    guard_policies=guard_policies,
                )
            target_path = target.normalized_value or (
                str(guard_policies[index].normalized_path)
                if index < len(guard_policies)
                else target.value
            )
            if not is_auto_memory_markdown_path(target_path, workspace):
                return PermissionDecision(
                    action="deny",
                    reason="Long-term memory extraction agent cannot write outside .onecode/memory Markdown files.",
                    source="long_term_memory_extraction_agent",
                    targets=targets,
                    guard_policies=guard_policies,
                )
        return PermissionDecision(
            action="allow",
            reason="Long-term memory extraction agent may write memory Markdown files.",
            source="long_term_memory_extraction_agent",
            targets=targets,
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
        if response.action == "allow":
            for update in response.permission_updates:
                if update.destination == "projectSettings":
                    if self.project_store is None:
                        raise ValueError(
                            "Project permission update requested but no project settings store is configured."
                        )
                    self.project_store.apply_update(update)
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
        return (
            self.session_store.is_tool_denied(tool_name)
            or self._is_project_tool_denied(tool_name)
            or tool_name in _names(state.metadata.get("denied_tools"))
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

    def _denied_skill_name(
        self,
        classification: ToolCallClassification,
        state: RuntimeState,
    ) -> str | None:
        denied = _names(state.metadata.get("denied_skills"))
        for target in classification.targets:
            if target.kind != "session_state" or target.operation != "skill_load":
                continue
            skill_name = target.value.lstrip("/")
            if self.session_store.is_skill_denied(skill_name) or skill_name in denied:
                return skill_name
        return None

    def _ask_reasons(
        self,
        *,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
        state: RuntimeState,
    ) -> list[str]:
        reasons: list[str] = []
        project_asks = self._matching_project_rules(
            "ask",
            descriptor=descriptor,
            classification=classification,
            guard_policies=guard_policies,
        )
        if project_asks:
            reasons.append(_project_rule_reason("ask", project_asks))
        for target in classification.targets:
            if (
                target.kind == "command"
                and target.operation == "execute"
                and not classification.read_only
            ):
                reasons.append(
                    "Command may modify system state or has unknown side effects."
                )
            if (
                target.kind == "external_service"
                and target.operation == "call"
                and not classification.read_only
            ):
                reasons.append(
                    "MCP tool may change external service state or has unknown side effects."
                )
        for policy in guard_policies:
            protected = _protected_project_dir(
                policy.normalized_path,
                self.protected_project_dirs,
            )
            if (
                protected is not None
                and not _is_session_tool_result_read(policy, state)
                and not _is_long_term_memory_project_path(policy, self.project_store)
            ):
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

    def _project_rules(self, behavior: PermissionBehavior) -> tuple[PermissionRule, ...]:
        if self.project_store is None:
            return ()
        return tuple(
            rule
            for rule in self.project_store.load_rules()
            if rule.behavior == behavior
        )

    def _is_project_tool_denied(self, tool_name: str) -> bool:
        return any(
            rule.value.tool_name == tool_name and rule.value.rule_content is None
            for rule in self._project_rules("deny")
        )

    def _matching_project_rules(
        self,
        behavior: PermissionBehavior,
        *,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
    ) -> tuple[PermissionRule, ...]:
        matches: list[PermissionRule] = []
        for rule in self._project_rules(behavior):
            if rule.value.tool_name != descriptor.name:
                continue
            if rule.value.rule_content is None:
                matches.append(rule)
                continue
            if _rule_content_matches(
                rule.value.rule_content,
                classification=classification,
                guard_policies=guard_policies,
            ):
                matches.append(rule)
        return tuple(matches)

    def _project_allows_call(
        self,
        *,
        descriptor: ToolDescriptor,
        classification: ToolCallClassification,
        guard_policies: tuple[GuardPolicy, ...],
    ) -> bool:
        allow_rules = tuple(
            rule
            for rule in self._project_rules("allow")
            if rule.value.tool_name == descriptor.name
        )
        if not allow_rules:
            return False
        if any(rule.value.rule_content is None for rule in allow_rules):
            return True

        patterns = tuple(
            rule.value.rule_content
            for rule in allow_rules
            if rule.value.rule_content is not None
        )
        match_values = _rule_match_values(classification, guard_policies)
        if not match_values:
            return False
        return all(
            any(_rule_value_matches(value, pattern) for pattern in patterns)
            for value in match_values
        )

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


def _is_session_tool_result_read(policy: GuardPolicy, state: RuntimeState) -> bool:
    if policy.operation not in {"read", "list"}:
        return False
    session_id = state.session_id
    parts = [part.lower() for part in resolve_path(policy.normalized_path).parts]
    for index, part in enumerate(parts):
        if part != ".onecode":
            continue
        if index + 2 >= len(parts):
            continue
        if parts[index + 1] == session_id.lower() and parts[index + 2] == "tool-results":
            return True
    return False


def _is_long_term_memory_project_path(
    policy: GuardPolicy,
    project_store: ProjectPermissionSettingsStore | None,
) -> bool:
    if project_store is None:
        return False
    workspace = _workspace_from_project_store(project_store)
    if workspace is None:
        return False
    return is_auto_memory_path(policy.normalized_path, workspace)


def _workspace_from_project_store(
    project_store: ProjectPermissionSettingsStore,
) -> Path | None:
    try:
        return resolve_path(project_store.settings_path.parent.parent)
    except Exception:
        return None


def _workspace_from_memory_dir(memory_dir: Path) -> Path:
    resolved = resolve_path(memory_dir)
    if resolved.name.lower() == "memory" and resolved.parent.name.lower() == ".onecode":
        return resolved.parent.parent
    return resolved.parent


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


def _rule_content_matches(
    pattern: str,
    *,
    classification: ToolCallClassification,
    guard_policies: tuple[GuardPolicy, ...],
) -> bool:
    return any(
        _rule_value_matches(value, pattern)
        for value in _rule_match_values(classification, guard_policies)
    )


def _rule_value_matches(value: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(value, pattern):
        return True
    if pattern.endswith(":*"):
        prefix = pattern[:-2]
        return value == prefix or value.startswith(f"{prefix} ")
    return False


def _rule_match_values(
    classification: ToolCallClassification,
    guard_policies: tuple[GuardPolicy, ...],
) -> tuple[str, ...]:
    values: list[str] = []
    for target in classification.targets:
        values.append(target.value)
        if target.normalized_value:
            values.append(target.normalized_value)
    for policy in guard_policies:
        values.append(policy.original_path)
        values.append(str(policy.normalized_path))
    return tuple(_dedupe([value for value in values if value]))


def _rule_strings(rules: tuple[PermissionRule, ...]) -> tuple[str, ...]:
    from services.permissions.rules import permission_rule_value_to_string

    return tuple(permission_rule_value_to_string(rule.value) for rule in rules)


def _project_rule_reason(
    behavior: PermissionBehavior,
    rules: tuple[PermissionRule, ...],
) -> str:
    return f"Project permission settings matched {behavior}: {', '.join(_rule_strings(rules))}"
