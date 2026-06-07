from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core.runtime_state import RuntimeState
from services.attachments.projector import AttachmentProjector
from services.skills import SkillCommand
from services.subagents.types import SubagentResult
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import ToolCall
from tools.skill import descriptor as skill_descriptor


class FakeSkillProvider:
    def __init__(self, skills: tuple[SkillCommand, ...]) -> None:
        self.skills = {skill.name: skill for skill in skills}

    def find_skill(self, name: str, cwd: Path):
        return self.skills.get(name.lstrip("/"))

    def visible_skills(self, state: RuntimeState, cwd: Path):
        return tuple(self.skills.values())


class FakeForkRunner:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run_skill(self, **kwargs):
        self.calls.append(kwargs)
        return SubagentResult(
            agent_type="skill:plan",
            session_id="child-session",
            final_text="child summary",
            transition="completed",
            tool_result_count=0,
        )


def execute_one(registry: ToolRegistry, call: ToolCall, state: RuntimeState):
    async def collect():
        executor = RegistryToolExecutor(registry)
        results = []
        async for update in executor.execute((call,), state):
            if update.result is not None:
                results.append(update.result)
        return results[0]

    return asyncio.run(collect())


def test_skill_tool_inline_returns_short_result_and_skill_attachment(
    tmp_path: Path,
) -> None:
    provider = FakeSkillProvider(
        (
            SkillCommand(
                name="code-review",
                description="Review code changes",
                content="Follow this review checklist.",
                source="project",
                root=tmp_path / ".onecode" / "skills" / "code-review",
                allowed_tools=("grep",),
            ),
        )
    )
    registry = ToolRegistry(
        [skill_descriptor(skill_provider=provider, cwd=lambda: tmp_path)]
    )

    result = execute_one(
        registry,
        ToolCall(id="call-skill", name="skill", input={"skill": "/code-review"}),
        RuntimeState(),
    )

    assert result.is_error is False
    assert result.content == "Launching skill: code-review"
    assert result.metadata["allowed_tools"] == ("grep",)
    assert len(result.followup_messages) == 1
    attachment = result.followup_messages[0]
    assert attachment["role"] == "attachment"
    assert attachment["attachment"]["type"] == "skill"
    assert attachment["attachment"]["skill_name"] == "code-review"

    projected = AttachmentProjector().project((attachment,), RuntimeState())
    assert [message["role"] for message in projected] == ["user"]
    assert "[skill loaded: code-review]" in projected[0]["content"]
    assert "Follow this review checklist." in projected[0]["content"]


def test_skill_tool_fork_returns_child_summary_without_attachment(tmp_path: Path) -> None:
    runner = FakeForkRunner()
    provider = FakeSkillProvider(
        (
            SkillCommand(
                name="plan",
                description="Plan work",
                content="Plan in a clean context.",
                source="project",
                context="fork",
            ),
        )
    )
    registry = ToolRegistry(
        [
            skill_descriptor(
                skill_provider=provider,
                cwd=lambda: tmp_path,
                fork_runner=runner,
            )
        ]
    )

    result = execute_one(
        registry,
        ToolCall(id="call-skill", name="skill", input={"skill": "plan", "args": "now"}),
        RuntimeState(session_id="parent-session"),
    )

    payload = json.loads(result.content)
    assert result.is_error is False
    assert payload["child_session_id"] == "child-session"
    assert payload["final_text"] == "child summary"
    assert result.followup_messages == ()
    assert runner.calls[0]["args"] == "now"
