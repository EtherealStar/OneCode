"""Provider-visible text rendered for plan-mode attachments.

The actual message construction lives in ``services.attachments.projector``.
This module just owns the human-readable prose so the prompt can be unit-tested
without touching the projector.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_plan_mode_intro(
    plan_path: Path,
    *,
    plan_content: str = "",
) -> str:
    """Initial plan-mode message: orient the model and announce the file."""

    plan_section = _format_plan_section(plan_content)
    return (
        "<plan_mode>\n"
        "You are now in plan mode. You MUST NOT make any edits, run any "
        "non-readonly tools, or otherwise modify the system, with one "
        "exception: you may edit the plan file at the path below.\n\n"
        "Workflow:\n"
        "1. Use read-only tools (read_file, glob, grep, bash with read-only "
        "commands, and explore agents) to investigate the codebase and the "
        "request.\n"
        "2. Write your plan to the plan file using write_file or edit_file. "
        "The plan file is the only file you are allowed to write to.\n"
        "3. If you encounter a decision only the user can make, call the "
        "ask_user_question tool to collect a structured answer.\n"
        "4. When the plan is complete, call exit_plan_mode to ask the user to "
        "approve it. Do not implement the plan yourself.\n\n"
        f"Plan file: {plan_path}\n"
        f"{plan_section}\n"
        "When you have nothing more to investigate, end your turn so the user "
        "can review your plan.\n"
        "</plan_mode>"
    )


def render_plan_mode_reentry(plan_path: Path, plan_content: str) -> str:
    """Re-entry message when the user rejects the plan or refreshes it."""

    plan_section = _format_plan_section(plan_content)
    return (
        "<plan_mode_reentry>\n"
        "You are still in plan mode. Read the existing plan below and decide "
        "whether to continue editing it, replace it, or ask the user a "
        "clarifying question before submitting again. You MUST NOT implement "
        "the plan or modify files other than this plan file.\n\n"
        f"Plan file: {plan_path}\n"
        f"{plan_section}\n"
        "Workflow:\n"
        "1. Use ask_user_question when the user needs to clarify intent.\n"
        "2. Update the plan with write_file or edit_file when ready.\n"
        "3. Call exit_plan_mode to request approval again.\n"
        "</plan_mode_reentry>"
    )


def render_plan_mode_exit(plan_path: Path, plan_content: str) -> str:
    """Post-approval message: tells the model it may now implement."""

    plan_section = _format_plan_section(plan_content)
    return (
        "<plan_mode_exit>\n"
        "The user approved your plan. You have exited plan mode and may now "
        "implement it. The plan file remains on disk for reference.\n\n"
        f"Plan file: {plan_path}\n"
        f"{plan_section}\n"
        "</plan_mode_exit>"
    )


def _format_plan_section(plan_content: str) -> str:
    if not plan_content.strip():
        return "Current plan contents: (empty — write the plan to this file.)"
    return (
        "Current plan contents:\n"
        "----\n"
        f"{plan_content.rstrip()}\n"
        "----"
    )
