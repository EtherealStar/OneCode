"""计划模式附件注入辅助函数。

这些辅助函数位于 services/plans 中，因为它们属于计划模式生命周期的一部分，
而非通用附件流水线。它们用于构建持久附件载荷，供 ui.cli 及其他调用方传递给
AgentLoop.stream(prompt, attachments=...)，从而使计划消息像其他附件一样进入
运行记录。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.runtime_state import PlanState, RuntimeState
from services.plans.attachments import (
    build_plan_mode_attachment,
    build_plan_mode_exit_attachment,
    build_plan_mode_reentry_attachment,
)
from services.plans.store import PlanStore
from services.plans.transitions import (
    consume_plan_mode_attachment,
    consume_plan_mode_exit_attachment,
)


def build_plan_attachments_for_state(
    state: RuntimeState,
    plan_store: PlanStore,
) -> list[dict[str, Any]]:
    """返回在模型轮次开始前需要注入的持久附件消息。

    该函数由 CLI 或 REPL 在调用 agent 循环前调用。state.plan 上的标志位会被
    原子性消费，因此单次 /plan 命令每轮仅注入一次附件。
    """

    attachments: list[dict[str, Any]] = []
    if consume_plan_mode_attachment(state):
        attachment, _ = _intro_or_reentry(state, plan_store)
        if attachment is not None:
            attachments.append(attachment)
    if consume_plan_mode_exit_attachment(state):
        attachment = _exit_attachment(state, plan_store)
        if attachment is not None:
            attachments.append(attachment)
    return attachments


def _intro_or_reentry(
    state: RuntimeState,
    plan_store: PlanStore,
) -> tuple[dict[str, Any] | None, PlanState]:
    plan_state = state.plan
    if plan_state.plan_slug is None:
        # 尚未分配计划文件。调用方应已通过 enter_plan_mode 完成分配，此处视为无操作。
        return None, plan_state
    plan_file = plan_store.read_plan(state)
    content = plan_file.read() if plan_file.exists() else ""
    # 若存在已有计划内容，则使用 reentry 变体，以便模型感知当前是在编辑已有计划而非重新开始。
    if content.strip() and plan_state.has_exited_plan_mode is False:
        return (
            build_plan_mode_reentry_attachment(Path(plan_file.path), content),
            plan_state,
        )
    return (
        build_plan_mode_attachment(Path(plan_file.path), plan_content=content),
        plan_state,
    )


def _exit_attachment(
    state: RuntimeState,
    plan_store: PlanStore,
) -> dict[str, Any] | None:
    if state.plan.plan_slug is None:
        return None
    plan_file = plan_store.read_plan(state)
    content = plan_file.read() if plan_file.exists() else ""
    return build_plan_mode_exit_attachment(Path(plan_file.path), content)