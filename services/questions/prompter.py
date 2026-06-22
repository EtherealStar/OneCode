"""Prompter protocol for structured user questions."""

from __future__ import annotations

from typing import Protocol

from services.questions.types import QuestionRequest, QuestionResponse


class UserQuestionPrompter(Protocol):
    """Collect structured user answers for ``ask_user_question``."""

    async def ask_questions(
        self,
        questions: tuple[QuestionRequest, ...],
    ) -> QuestionResponse:
        ...
