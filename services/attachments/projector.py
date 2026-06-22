"""Project durable attachment messages into provider-visible messages."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.runtime_state import RuntimeState
from services.plans.attachments import (
    build_plan_mode_attachment,
    build_plan_mode_exit_attachment,
    build_plan_mode_reentry_attachment,
)


class AttachmentProjector:
    def project(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
    ) -> tuple[dict[str, Any], ...]:
        """Replace internal attachment roles before provider payload creation."""

        _ = state
        projected: list[dict[str, Any]] = []
        for message in messages:
            if message.get("role") != "attachment":
                projected.append(deepcopy(message))
                continue
            projected.extend(_project_attachment_message(message))
        return tuple(projected)


def _project_attachment_message(message: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    attachment = message.get("attachment")
    if not isinstance(attachment, dict):
        return (_notice("Unsupported attachment message with missing payload."),)
    attachment_type = attachment.get("type")
    if attachment_type == "file":
        return _project_file(attachment, message)
    if attachment_type == "skill":
        return (_project_skill(attachment),)
    if attachment_type == "directory":
        return (_project_directory(attachment),)
    if attachment_type == "edited_text_file":
        return (_project_edited_text_file(attachment),)
    if attachment_type == "queued_command":
        return (
            _notice(
                f"[queued command from coordinator]\n{attachment.get('content', '')}"
            ),
        )
    if attachment_type == "background_task_notification":
        return (_notice(_background_task_notification_content(attachment)),)
    if attachment_type in {"relevant_memories", "nested_memory"}:
        return (_notice(_memory_content(attachment)),)
    if attachment_type == "plan_mode":
        return _project_plan_mode(attachment)
    if attachment_type == "hook_result":
        return (_notice(f"[hook result]\n{attachment.get('content', '')}"),)
    if attachment_type == "attachment_error":
        return (_notice(_error_content(attachment)),)
    return (_notice(f"Unsupported attachment type: {attachment_type}"),)


def _project_plan_mode(attachment: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Render the new structured plan-mode attachment into a provider-visible
    user message.

    The projector treats plan_mode as a first-class attachment variant. We
    rebuild the payload through ``services.plans.attachments`` so the
    formatting stays in one place; legacy attachments (no ``variant`` key)
    fall back to the intro form.
    """

    plan_path = str(attachment.get("plan_path", ""))
    plan_content = str(attachment.get("plan_content", ""))
    variant = str(attachment.get("variant", "intro"))

    if variant == "reentry":
        payload = build_plan_mode_reentry_attachment(plan_path, plan_content)
    elif variant == "exit":
        payload = build_plan_mode_exit_attachment(plan_path, plan_content)
    else:
        payload = build_plan_mode_attachment(plan_path, plan_content=plan_content)

    return (
        {
            "role": "user",
            "content": payload["content"],
            "metadata": {
                "synthetic": True,
                "source": "attachment",
                "attachment_type": "plan_mode",
                "plan_path": plan_path,
                "variant": variant,
            },
        },
    )


def _project_file(
    attachment: dict[str, Any],
    message: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    metadata = (
        message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    )
    attachment_id = str(
        metadata.get("attachment_id") or attachment.get("id") or "unknown"
    )
    call_id = f"attachment_read_{attachment_id}"
    path = str(attachment.get("path", ""))
    arguments: dict[str, Any] = {"file_path": path}
    offset = attachment.get("offset")
    limit = attachment.get("limit")
    if isinstance(offset, int):
        arguments["offset"] = offset
    if isinstance(limit, int):
        arguments["limit"] = limit
    details = "\n".join(f"- {key}: {value}" for key, value in arguments.items())
    notice = (
        "[attachment file]\n"
        f"Synthetic call id: {call_id}\n"
        "Equivalent tool: read_file\n"
        f"{details}\n\n"
        "Result:\n"
        f"{attachment.get('content', '')}"
    )
    if attachment.get("truncated") is True:
        notice += (
            "\n\n[Attachment content was truncated; use read_file if more detail is needed.]"
        )
    return (
        {
            "role": "user",
            "content": notice,
            "metadata": {
                "synthetic": True,
                "source": "attachment",
                "attachment_type": "file",
            },
        },
    )


def _project_directory(attachment: dict[str, Any]) -> dict[str, Any]:
    entries = attachment.get("entries")
    names = (
        "\n".join(f"- {name}" for name in entries)
        if isinstance(entries, list)
        else ""
    )
    truncated = "\n[Directory listing truncated]" if attachment.get("truncated") else ""
    return _notice(
        f"[directory attachment]\nPath: {attachment.get('path', '')}\nEntries:\n{names}{truncated}"
    )


def _project_edited_text_file(attachment: dict[str, Any]) -> dict[str, Any]:
    return _notice(
        "A previously read text file was edited outside the model-visible tool "
        f"call flow.\nPath: {attachment.get('path', '')}\nDiff:\n{attachment.get('diff', '')}"
    )


def _project_skill(attachment: dict[str, Any]) -> dict[str, Any]:
    """Project a loaded skill as runtime-provided user context."""

    args = str(attachment.get("args", ""))
    source = str(attachment.get("source", ""))
    name = str(attachment.get("skill_name", "unknown"))
    content = str(attachment.get("content", ""))
    return {
        "role": "user",
        "content": (
            f"[skill loaded: {name}]\n"
            f"Arguments: {args}\n"
            f"Source: {source}\n\n"
            f"{content}"
        ),
        "metadata": {
            "synthetic": True,
            "source": "attachment",
            "attachment_type": "skill",
        },
    }


def _notice(content: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": content,
        "metadata": {"synthetic": True, "source": "attachment"},
    }


def _memory_content(attachment: dict[str, Any]) -> str:
    return (
        f"[memory attachment]\nPath: {attachment.get('path', '')}\n"
        f"{attachment.get('content', '')}"
    )


def _background_task_notification_content(attachment: dict[str, Any]) -> str:
    return "\n".join(
        [
            "<task_notification>",
            f"<task_id>{attachment.get('task_id', '')}</task_id>",
            f"<task_type>{attachment.get('task_type', '')}</task_type>",
            f"<output_file>{attachment.get('output_file', '')}</output_file>",
            f"<status>{attachment.get('status', '')}</status>",
            f"<summary>{attachment.get('summary', '')}</summary>",
            "</task_notification>",
        ]
    )


def _error_content(attachment: dict[str, Any]) -> str:
    return (
        "[attachment resolution error]\n"
        f"Mention: {attachment.get('mention', '')}\n"
        f"Error: {attachment.get('error', '')}\n"
        f"Message: {attachment.get('message', '')}"
    )
