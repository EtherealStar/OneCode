"""exit_plan_mode 的工具描述符。

handler 本身并不直接向用户提示请求审批：该逻辑位于 CLI 的 /plan 退出流程中，
以确保用户始终看到专用且无法意外绕过的计划审批界面。该工具的职责是：

1. 当运行时不处于计划模式时拒绝运行。
2. 读取计划文件并向模型回报。
3. 等待 approved=True/False（由 CLI 驱动）并应用状态迁移。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

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


def descriptor(plan_store: PlanStore) -> ToolDescriptor:
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


def _handle_for(plan_store: PlanStore):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:

        if runtime.state.permission_mode != PermissionMode.PLAN:
            payload = {
                "error": "not_in_plan_mode",
                "message": ("exit_plan_mode requires the runtime to be in plan mode."),
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

        # 此处不调用 exit_plan_mode(approved=...)。CLI 流程会在其权限提示器中
        # 拦截 exit_plan_mode 并携带用户的决策重新调用该工具。如果模型直接调用
        # 该工具（无 CLI 提示器），则报告 "awaiting_approval" 并让运行时保持在计划模式。
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
