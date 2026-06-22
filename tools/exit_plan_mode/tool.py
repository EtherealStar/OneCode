"""Tool descriptor for ``exit_plan_mode``.

The handler does NOT itself prompt the user for approval: that lives in the CLI
``/plan`` exit flow so the user always sees a dedicated plan approval surface
that they cannot accidentally bypass. The tool's job is to:

1. Refuse to run when the runtime is not in plan mode.
2. Read the plan file and report it back to the model.
3. Wait for ``approved=True/False`` (driven by the CLI) and apply the
   state transition.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from core.runtime_state import PermissionMode
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.exit_plan_mode.prompt import PROMPT

if TYPE_CHECKING:
    from services.plans.store import PlanStore


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
    },
    "additionalProperties": False,
}


def descriptor(plan_store: "PlanStore") -> ToolDescriptor:
    return ToolDescriptor(
        name="exit_plan_mode",
        description=(
            "Request user approval of the current plan and, on approval, "
            "leave plan mode so the agent can begin implementation."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_handle_for(plan_store),
        prompt=PROMPT,
        search_hint="exit plan mode",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _handle_for(plan_store: "PlanStore"):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        from services.plans.transitions import exit_plan_mode

        if runtime.state.permission_mode != PermissionMode.PLAN:
            payload = {
                "error": "not_in_plan_mode",
                "message": (
                    "exit_plan_mode requires the runtime to be in plan mode."
                ),
            }
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="exit_plan_mode",
                content=json.dumps(payload, ensure_ascii=False),
                is_error=True,
                metadata={"error": "not_in_plan_mode"},
            )

        plan_file = plan_store.read_plan(runtime.state)
        plan_content = plan_file.read()
        summary = str(tool_input.get("summary", "")).strip()

        # We do NOT call ``exit_plan_mode(approved=...)`` here. The CLI flow
        # intercepts exit_plan_mode in its permission prompter and re-invokes
        # the tool with the user's decision. If the model calls the tool
        # directly (no CLI prompter), we report "awaiting approval" and let
        # the runtime stay in plan mode.
        payload = {
            "status": "awaiting_approval",
            "plan_path": str(plan_file.path),
            "plan_slug": plan_file.slug,
            "summary": summary,
            "plan_excerpt": _excerpt(plan_content),
        }
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="exit_plan_mode",
            content=json.dumps(payload, ensure_ascii=False),
            metadata={
                "plan_path": str(plan_file.path),
                "awaiting_approval": True,
            },
        )

    return handle


def _excerpt(content: str) -> str:
    stripped = content.strip()
    if not stripped:
        return "(empty plan file)"
    if len(stripped) > 800:
        return stripped[:797] + "..."
    return stripped


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    _ = runtime
    summary = tool_input.get("summary")
    if summary is not None and (not isinstance(summary, str)):
        return ValidationResult.failure("summary must be a string when provided.")
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    return ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="mutate_state",
                value="permission_mode",
            ),
        ),
        permission_subject="exit_plan_mode",
    )
