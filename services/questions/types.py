"""Types for the structured user-question prompter.

The prompter protocol is intentionally narrow: it takes a list of questions
with options and returns either a list of ``AnswerRecord`` entries or a single
``declined=True`` response. The model never sees raw exception text; tools
translate the response into a normal ``ToolExecutionResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QuestionOption:
    """One selectable option for a question."""

    label: str
    description: str = ""
    preview: str | None = None


@dataclass(frozen=True)
class QuestionRequest:
    """A single structured question for the user."""

    question: str
    header: str
    options: tuple[QuestionOption, ...]
    multi_select: bool = False


@dataclass(frozen=True)
class AnswerRecord:
    """The user's answer to a single question."""

    question: str
    answer: str | tuple[str, ...]


@dataclass(frozen=True)
class QuestionResponse:
    """Aggregate response from the user-question prompter."""

    answers: tuple[AnswerRecord, ...] = ()
    declined: bool = False
    feedback: str = ""


class UserQuestionError(RuntimeError):
    """Raised when the prompter cannot complete a structured question round."""


__all__ = [
    "AnswerRecord",
    "QuestionOption",
    "QuestionRequest",
    "QuestionResponse",
    "UserQuestionError",
]
