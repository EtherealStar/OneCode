"""结构化用户提问的交互提示器协议。"""

from __future__ import annotations

from typing import Protocol

from services.questions.types import QuestionRequest, QuestionResponse


class UserQuestionPrompter(Protocol):
    """为 ask_user_question 收集结构化用户回答。"""

    async def ask_questions(
        self,
        questions: tuple[QuestionRequest, ...],
    ) -> QuestionResponse: ...
