"""Plan-mode attachment injection helpers.

These helpers live in ``services/plans`` because they are part of the
plan-mode lifecycle, not the generic attachment pipeline. They build the
durable attachment payloads that ``ui.cli`` and other callers pass to
``AgentLoop.stream(prompt, attachments=...)`` so the plan message enters the
transcript like any other attachment.
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
    """Return the durable attachment messages to inject before a model turn.

    This is called by the CLI/repl right before invoking the agent loop. The
    flags on ``state.plan`` are atomically consumed, so a single ``/plan``
    command only injects the attachment once per turn.
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
        # No plan file allocated yet — caller should have done this via
        # ``enter_plan_mode`` already; treat this as a no-op.
        return None, plan_state
    plan_file = plan_store.read_plan(state)
    content = plan_file.read() if plan_file.exists() else ""
    # If there is existing plan content, use the reentry variant so the
    # model understands it's editing a known plan rather than starting fresh.
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