"""Plan-mode services: file store, transitions, prompts, and attachments.

The plan store owns ``.onecode/plans/`` markdown files. Plan state itself lives
on ``core.runtime_state.RuntimeState.plan``; this package is a pure filesystem
and prompt layer so it can be tested without a running runtime.
"""

from services.plans.injection import build_plan_attachments_for_state
from services.plans.store import PlanStore, PlanStoreError
from services.plans.transitions import (
    enter_plan_mode,
    exit_plan_mode,
    request_plan_mode_attachment,
    consume_plan_mode_attachment,
    consume_plan_mode_exit_attachment,
)

__all__ = [
    "PlanStore",
    "PlanStoreError",
    "build_plan_attachments_for_state",
    "enter_plan_mode",
    "exit_plan_mode",
    "request_plan_mode_attachment",
    "consume_plan_mode_attachment",
    "consume_plan_mode_exit_attachment",
]
