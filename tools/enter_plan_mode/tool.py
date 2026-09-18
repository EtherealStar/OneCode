"""enter_plan_mode 的工具描述符。

handler 保持轻量设计：它从不执行用户代码，仅变更 RuntimeState
并在下一轮生成持久化附件。真正的权限强制在执行器与权限策略中完成；
工具本身是模型请求计划模式时的入口点。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TYPE_CHECKING

from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.enter_plan_mode.prompt import PROMPT

if TYPE_CHECKING:
    from services.plans.store import PlanStore

INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reason": {"type": "string"},
    },
    "additionalProperties": False,
}


def descriptor(plan_store: "PlanStore") -> ToolDescriptor:
    return ToolDescriptor(
        name="enter_plan_mode",
        description=(
            "Switch the runtime into plan mode. The agent becomes read-only "
            "except for writing the plan file at .onecode/plans/<slug>.md."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_handle_for(plan_store),
        prompt=PROMPT,
        search_hint="enter plan mode",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _handle_for(plan_store: "PlanStore"):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        from services.plans.transitions import enter_plan_mode

        transition = enter_plan_mode(runtime.state, plan_store)
        payload = {
            "permission_mode": "plan",
            "plan_path": str(transition.plan_file.path),
            "plan_slug": transition.plan_file.slug,
            "pre_plan_mode": transition.pre_plan_mode.value,
            "already_in_plan_mode": _was_already_in_plan_mode(
                runtime.state,
            ),
        }
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="enter_plan_mode",
            content=json.dumps(payload, ensure_ascii=False),
            metadata={
                "plan_path": payload["plan_path"],
                "permission_mode": "plan",
            },
        )

    return handle


def _was_already_in_plan_mode(state: Any) -> bool:
    # 在 enter_plan_mode 执行后，permission_mode 为 PLAN。我们依赖
    # 调用方在调用前进行快照以计算差异；此处仅报告新状态。
    _ = state
    return False


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    _ = runtime
    reason = tool_input.get("reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        return ValidationResult.failure("reason must be a non-empty string when provided.")
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    return ToolCallClassification(
        # 进入计划模式纯粹是运行时状态变更；它不触碰
        # 文件系统也不运行任意代码，因此将其分类为并发安全的内部变更。
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
        permission_subject="enter_plan_mode",
    )