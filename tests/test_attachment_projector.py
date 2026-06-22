from __future__ import annotations

from core.runtime_state import RuntimeState
from services.attachments.projector import AttachmentProjector
from services.attachments.types import AttachmentMessage


def test_projects_file_attachment_to_provider_safe_user_context() -> None:
    attachment = AttachmentMessage(
        attachment={
            "type": "file",
            "path": "D:\\study\\OneCode\\note.txt",
            "content": "1\tone",
            "offset": 1,
            "limit": 1,
        },
        attachment_id="att_123",
        source="user_input",
    ).to_message()

    projected = AttachmentProjector().project((attachment,), RuntimeState())

    assert [message["role"] for message in projected] == ["user"]
    user = projected[0]
    assert "attachment_read_att_123" in user["content"]
    assert "Equivalent tool: read_file" in user["content"]
    assert "- file_path: D:\\study\\OneCode\\note.txt" in user["content"]
    assert "1\tone" in user["content"]
    assert "tool_calls" not in user
    assert user["metadata"]["synthetic"] is True
    assert user["metadata"]["attachment_type"] == "file"


def test_projector_drops_raw_attachment_role() -> None:
    attachment = AttachmentMessage(
        attachment={
            "type": "plan_mode",
            "variant": "intro",
            "plan_path": "/tmp/plan.md",
            "content": "",
        },
        attachment_id="att_plan",
        source="plan_mode",
    ).to_message()

    projected = AttachmentProjector().project((attachment,), RuntimeState())

    assert projected
    assert all(message.get("role") != "attachment" for message in projected)
    user = projected[0]
    assert user["role"] == "user"
    assert user["metadata"]["attachment_type"] == "plan_mode"
    assert user["metadata"]["variant"] == "intro"
    assert "<plan_mode>" in user["content"]
    assert "/tmp/plan.md" in user["content"]
    assert "ask_user_question" in user["content"]
    assert "exit_plan_mode" in user["content"]


def test_projector_renders_reentry_and_exit_variants() -> None:
    state = RuntimeState()
    state.plan.plan_slug = "demo"
    intro = AttachmentMessage(
        attachment={
            "type": "plan_mode",
            "variant": "reentry",
            "plan_path": "/tmp/demo.md",
            "plan_content": "# Demo plan\n- step 1",
        },
        attachment_id="att_re",
        source="plan_mode",
    ).to_message()
    exit_ = AttachmentMessage(
        attachment={
            "type": "plan_mode",
            "variant": "exit",
            "plan_path": "/tmp/demo.md",
            "plan_content": "# Demo plan\n- step 1",
        },
        attachment_id="att_exit",
        source="plan_mode",
    ).to_message()
    projected = AttachmentProjector().project((intro, exit_), state)
    contents = [message["content"] for message in projected]
    assert any("<plan_mode_reentry>" in content for content in contents)
    assert any("<plan_mode_exit>" in content for content in contents)
    assert any("user approved" in content.lower() for content in contents)


def test_projects_background_task_notification_to_xml() -> None:
    attachment = AttachmentMessage(
        attachment={
            "type": "background_task_notification",
            "task_id": "b_1234",
            "task_type": "local_bash",
            "status": "completed",
            "summary": "done",
            "output_file": ".onecode/session/background-tasks/b_1234.output",
        },
        attachment_id="att_task",
        source="runtime",
    ).to_message()

    projected = AttachmentProjector().project((attachment,), RuntimeState())

    assert projected[0]["role"] == "user"
    assert "<task_notification>" in projected[0]["content"]
    assert "<task_id>b_1234</task_id>" in projected[0]["content"]
