"""结构化用户提问提示器所使用的类型定义。

该提示器协议保持严谨精简：接收带有选项的问题列表，并返回 AnswerRecord 条目列表或单项
declined=True 的响应。模型从不直接接收原始异常文本；工具会将响应转换为常规的
ToolExecutionResult。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QuestionOption:
    """问题的一个可选选项。"""

    label: str
    description: str = ""
    preview: str | None = None


@dataclass(frozen=True)
class QuestionRequest:
    """面向用户的单条结构化问题。"""

    question: str
    header: str
    options: tuple[QuestionOption, ...]
    multi_select: bool = False


@dataclass(frozen=True)
class AnswerRecord:
    """用户针对单条问题的回答。"""

    question: str
    answer: str | tuple[str, ...]


@dataclass(frozen=True)
class QuestionResponse:
    """用户提问提示器的聚合响应。"""

    answers: tuple[AnswerRecord, ...] = ()
    declined: bool = False
    feedback: str = ""


class UserQuestionError(RuntimeError):
    """当提示器无法完成一轮结构化提问时引发的异常。"""


__all__ = [
    "AnswerRecord",
    "QuestionOption",
    "QuestionRequest",
    "QuestionResponse",
    "UserQuestionError",
]
