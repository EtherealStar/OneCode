from __future__ import annotations

from core.runtime_state import RuntimeState
from services.attachments.projector import AttachmentProjector
from services.attachments.types import AttachmentMessage


def test_projects_file_attachment_to_synthetic_read_file_pair() -> None:
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

    assert [message["role"] for message in projected] == [
        "assistant",
        "tool_result",
    ]
    assistant, result = projected
    call = assistant["tool_calls"][0]
    assert call["id"] == "attachment_read_att_123"
    assert call["function"]["name"] == "read_file"
    assert result["tool_call_id"] == call["id"]
    assert result["tool_name"] == "read_file"
    assert result["content"] == "1\tone"
    assert result["metadata"]["synthetic"] is True


def test_projector_drops_raw_attachment_role() -> None:
    attachment = AttachmentMessage(
        attachment={"type": "plan_mode", "content": "reserved"},
        attachment_id="att_plan",
        source="plan_mode",
    ).to_message()

    projected = AttachmentProjector().project((attachment,), RuntimeState())

    assert projected
    assert all(message.get("role") != "attachment" for message in projected)
