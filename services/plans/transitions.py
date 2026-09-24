"""计划模式生命周期的结构化辅助函数。

这些辅助函数对模型完全无副作用：它们仅修改 RuntimeState.plan 和 permission_mode，
按需通过 PlanStore 操作文件系统，并返回足够的元数据供调用方（工具处理器、CLI
或附件投影器）响应。运行时循环从不直接导入这些函数，而是由工具和 CLI 负责调用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.runtime_state import PermissionMode, RuntimeState
from services.plans.store import PlanFile, PlanStore


@dataclass(frozen=True)
class PlanModeTransition:
    """计划模式生命周期调用的结果。

    调用方使用 plan_file 渲染 UI 提示，并使用 attachments 将相应的模型可见消息注入到下一轮次中。
    """

    plan_file: PlanFile
    pre_plan_mode: PermissionMode
    attachments: tuple[dict[str, Any], ...] = ()


def enter_plan_mode(
    state: RuntimeState,
    plan_store: PlanStore,
    *,
    requested_by: str = "tool",
) -> PlanModeTransition:
    """将运行时转换为计划模式并准备计划文件。

    具有幂等性：在已处于计划模式的运行时上调用此函数将返回已有计划文件并刷新附件标志。
    """

    if state.permission_mode != PermissionMode.PLAN:
        state.plan.pre_plan_mode = state.permission_mode
        state.permission_mode = PermissionMode.PLAN
    state.plan.has_exited_plan_mode = False
    state.plan.needs_plan_mode_attachment = True
    state.plan.needs_plan_mode_exit_attachment = False
    plan_file = plan_store.get_or_create_plan(state)
    _ = requested_by
    return PlanModeTransition(
        plan_file=plan_file,
        pre_plan_mode=state.plan.pre_plan_mode or PermissionMode.DEFAULT,
    )


def exit_plan_mode(
    state: RuntimeState,
    plan_store: PlanStore,
    *,
    approved: bool,
) -> PlanModeTransition:
    """退出计划模式，并请求在下一轮次中注入退出后附件。"""

    if state.permission_mode != PermissionMode.PLAN:
        raise ValueError("Cannot exit plan mode: runtime is not in plan mode.")
    plan_file = plan_store.read_plan(state)
    pre = state.plan.pre_plan_mode or PermissionMode.DEFAULT
    if approved:
        state.permission_mode = pre
        state.plan.has_exited_plan_mode = True
        state.plan.needs_plan_mode_attachment = False
        state.plan.needs_plan_mode_exit_attachment = True
    else:
        # 用户拒绝：保持在计划模式，刷新重入附件以便模型读取已有计划内容并进行调整。
        state.plan.needs_plan_mode_attachment = True
        state.plan.needs_plan_mode_exit_attachment = False
        state.plan.has_exited_plan_mode = False
    return PlanModeTransition(
        plan_file=plan_file,
        pre_plan_mode=pre,
    )


def request_plan_mode_attachment(state: RuntimeState) -> None:
    """标记下一模型轮次应当接收计划模式附件。"""

    if state.permission_mode == PermissionMode.PLAN:
        state.plan.needs_plan_mode_attachment = True


def consume_plan_mode_attachment(state: RuntimeState) -> bool:
    """以原子操作读取并清除计划模式附件标志。"""

    flag = state.plan.needs_plan_mode_attachment
    state.plan.needs_plan_mode_attachment = False
    return flag


def consume_plan_mode_exit_attachment(state: RuntimeState) -> bool:
    """以原子操作读取并清除退出计划模式后的附件标志。"""

    flag = state.plan.needs_plan_mode_exit_attachment
    state.plan.needs_plan_mode_exit_attachment = False
    return flag


def reset_plan_state(state: RuntimeState) -> None:
    """供 /clear 命令使用的公共辅助函数，避免直接篡改元数据。"""

    state.permission_mode = PermissionMode.DEFAULT
    state.plan.reset()
