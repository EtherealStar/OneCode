"""Structured helpers for the plan-mode lifecycle.

These helpers are deliberately side-effect free w.r.t. the model: they mutate
``RuntimeState.plan`` and ``permission_mode``, optionally touch the filesystem
via ``PlanStore``, and return enough metadata for the caller (a tool handler,
the CLI, or the attachment projector) to react. The runtime loop never imports
these directly; tools and the CLI do.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime_state import PermissionMode, PlanState, RuntimeState
from services.plans.store import PlanFile, PlanStore


@dataclass(frozen=True)
class PlanModeTransition:
    """Result of a plan-mode lifecycle call.

    The caller uses ``plan_file`` to render a UI hint and ``attachments`` to
    inject the appropriate provider-visible message into the next turn.
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
    """Transition the runtime into plan mode and prepare the plan file.

    Idempotent: calling this on a runtime that is already in plan mode returns
    the existing plan file and refreshes the attachment flag.
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
    """Leave plan mode and request the post-exit attachment on the next turn."""

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
        # User rejected: stay in plan mode, refresh the re-entry attachment so
        # the model can read existing plan content and adjust.
        state.plan.needs_plan_mode_attachment = True
        state.plan.needs_plan_mode_exit_attachment = False
        state.plan.has_exited_plan_mode = False
    return PlanModeTransition(
        plan_file=plan_file,
        pre_plan_mode=pre,
    )


def request_plan_mode_attachment(state: RuntimeState) -> None:
    """Mark that the next model turn should receive a plan-mode attachment."""

    if state.permission_mode == PermissionMode.PLAN:
        state.plan.needs_plan_mode_attachment = True


def consume_plan_mode_attachment(state: RuntimeState) -> bool:
    """Atomically read-and-clear the plan-mode attachment flag."""

    flag = state.plan.needs_plan_mode_attachment
    state.plan.needs_plan_mode_attachment = False
    return flag


def consume_plan_mode_exit_attachment(state: RuntimeState) -> bool:
    """Atomically read-and-clear the post-exit plan-mode attachment flag."""

    flag = state.plan.needs_plan_mode_exit_attachment
    state.plan.needs_plan_mode_exit_attachment = False
    return flag


def reset_plan_state(state: RuntimeState) -> None:
    """Public helper used by ``/clear`` so we never poke at metadata directly."""

    state.permission_mode = PermissionMode.DEFAULT
    state.plan.reset()
