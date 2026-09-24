"""为计划模式附件渲染的模型可见文本。

实际的消息构造逻辑位于 services.attachments.projector。
本模块仅维护可读文本内容，以便在不触及投影器的情况下对提示词进行单元测试。
"""

from __future__ import annotations

from pathlib import Path


def render_plan_mode_intro(
    plan_path: Path | str,
    *,
    plan_content: str = "",
) -> str:
    """初始计划模式消息：指引模型方向并通报计划文件路径。"""

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


def render_plan_mode_reentry(plan_path: Path | str, plan_content: str) -> str:
    """用户拒绝计划或刷新计划时的重入消息。"""

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


def render_plan_mode_exit(plan_path: Path | str, plan_content: str) -> str:
    """计划审批通过后的消息：告知模型现在可以开始执行实现。"""

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
    return f"Current plan contents:\n----\n{plan_content.rstrip()}\n----"
