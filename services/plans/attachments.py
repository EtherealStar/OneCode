"""Attachment payloads produced by the plan subsystem."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services.plans.prompts import (
    render_plan_mode_exit,
    render_plan_mode_intro,
    render_plan_mode_reentry,
)


def build_plan_mode_attachment(
    plan_path: Path,
    *,
    plan_content: str = "",
) -> dict[str, Any]:
    return {
        "type": "plan_mode",
        "variant": "intro",
        "plan_path": str(plan_path),
        "content": render_plan_mode_intro(plan_path, plan_content=plan_content),
    }


def build_plan_mode_reentry_attachment(
    plan_path: Path,
    plan_content: str,
) -> dict[str, Any]:
    return {
        "type": "plan_mode",
        "variant": "reentry",
        "plan_path": str(plan_path),
        "content": render_plan_mode_reentry(plan_path, plan_content),
    }


def build_plan_mode_exit_attachment(
    plan_path: Path,
    plan_content: str,
) -> dict[str, Any]:
    return {
        "type": "plan_mode",
        "variant": "exit",
        "plan_path": str(plan_path),
        "content": render_plan_mode_exit(plan_path, plan_content),
    }
