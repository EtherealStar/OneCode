from __future__ import annotations

from pathlib import Path

from core.runtime_state import RuntimeState
from prompts.assembler import DynamicPromptAssembler
from services.skills import SkillCommand


class FakeSkillProvider:
    def __init__(self, skills: tuple[SkillCommand, ...]) -> None:
        self.skills = skills

    def visible_skills(self, state: RuntimeState, cwd: Path):
        return self.skills

    def find_skill(self, name: str, cwd: Path):
        return next((skill for skill in self.skills if skill.name == name), None)


def test_dynamic_prompt_lists_skill_catalog_without_full_content(tmp_path: Path) -> None:
    provider = FakeSkillProvider(
        (
            SkillCommand(
                name="code-review",
                description="Review code changes",
                when_to_use="asked to review a diff",
                content="Follow this review checklist.",
                source="project",
            ),
        )
    )
    assembler = DynamicPromptAssembler(tmp_path, skill_provider=provider)

    prompt = assembler.assemble(RuntimeState())

    assert "# Available Skills\n" in prompt
    assert "- code-review: Review code changes - Use when asked to review a diff" in prompt
    assert "Follow this review checklist." not in prompt
