"""Tool descriptor for ``ask_user_question``.

The handler delegates to a ``UserQuestionPrompter`` provided by the runtime
(CLI TTY, batch harness, or test fake). The tool never prompts the user
itself; it just shapes the structured request and serializes the response.
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from services.questions.types import (
    QuestionOption,
    QuestionRequest,
    UserQuestionError,
)
from services.tools.types import (
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)
from tools.ask_user_question.prompt import PROMPT

if TYPE_CHECKING:
    from services.questions.prompter import UserQuestionPrompter


MAX_QUESTIONS = 4
MAX_OPTIONS = 4
MAX_HEADER_LEN = 12
MIN_OPTIONS = 2
MIN_QUESTIONS = 1


INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "header": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "description": {"type": "string"},
                                "preview": {"type": "string"},
                            },
                            "required": ["label"],
                            "additionalProperties": False,
                        },
                    },
                    "multi_select": {"type": "boolean"},
                },
                "required": ["question", "header", "options"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def descriptor(prompter: "UserQuestionPrompter") -> ToolDescriptor:
    return ToolDescriptor(
        name="ask_user_question",
        description=(
            "Ask the user one or more structured multiple-choice questions. "
            "Use during plan mode to clarify requirements before submitting."
        ),
        input_schema=INPUT_SCHEMA,
        handler=_handle_for(prompter),
        prompt=PROMPT,
        search_hint="ask user question",
        validate_input=_validate,
        classify_input=_classify_input,
    )


def _handle_for(prompter: "UserQuestionPrompter"):
    async def handle(
        tool_input: dict[str, Any],
        runtime: ToolRuntime,
    ) -> ToolExecutionResult:
        _ = runtime
        questions = _build_questions(tool_input.get("questions"))
        if not questions:
            payload = {
                "error": "no_questions",
                "message": "ask_user_question requires at least one question.",
            }
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="ask_user_question",
                content=json.dumps(payload, ensure_ascii=False),
                is_error=True,
                metadata={"error": "no_questions"},
            )
        try:
            response = await prompter.ask_questions(questions)
        except UserQuestionError as exc:
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="ask_user_question",
                content=json.dumps(
                    {"error": "user_question_error", "message": str(exc)},
                    ensure_ascii=False,
                ),
                is_error=True,
                metadata={"error": "user_question_error"},
            )
        except (EOFError, KeyboardInterrupt):
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="ask_user_question",
                content=json.dumps(
                    {
                        "status": "declined",
                        "reason": "User interrupted the question prompt.",
                    },
                    ensure_ascii=False,
                ),
                metadata={"status": "declined"},
            )
        if response.declined:
            payload = {
                "status": "declined",
                "feedback": response.feedback,
                "message": "User declined to answer. Continue without these "
                "answers or pick a safe default and explain your choice.",
            }
            return ToolExecutionResult(
                tool_call_id="",
                tool_name="ask_user_question",
                content=json.dumps(payload, ensure_ascii=False),
                metadata={"status": "declined"},
            )
        payload = {
            "status": "answered",
            "answers": [
                {"question": answer.question, "answer": answer.answer}
                for answer in response.answers
            ],
            "feedback": response.feedback,
        }
        return ToolExecutionResult(
            tool_call_id="",
            tool_name="ask_user_question",
            content=json.dumps(payload, ensure_ascii=False),
            metadata={"status": "answered"},
        )

    return handle


def _build_questions(raw: Any) -> tuple[QuestionRequest, ...]:
    if not isinstance(raw, list):
        return ()
    questions: list[QuestionRequest] = []
    for item in raw[:MAX_QUESTIONS]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        header = str(item.get("header", "")).strip()
        if not question or not header:
            continue
        if len(header) > MAX_HEADER_LEN:
            header = header[:MAX_HEADER_LEN]
        options_raw = item.get("options")
        if not isinstance(options_raw, list):
            continue
        options: list[QuestionOption] = []
        for entry in options_raw[:MAX_OPTIONS]:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label", "")).strip()
            if not label:
                continue
            description = str(entry.get("description", "")).strip()
            preview = entry.get("preview")
            options.append(
                QuestionOption(
                    label=label,
                    description=description,
                    preview=str(preview) if isinstance(preview, str) else None,
                )
            )
        if len(options) < MIN_OPTIONS:
            continue
        multi_select = bool(item.get("multi_select", False))
        questions.append(
            QuestionRequest(
                question=question,
                header=header,
                options=tuple(options),
                multi_select=multi_select,
            )
        )
    return tuple(questions)


def _validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
    _ = runtime
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return ValidationResult.failure("questions must be a non-empty array.")
    if len(questions) > MAX_QUESTIONS:
        return ValidationResult.failure(
            f"questions must contain at most {MAX_QUESTIONS} items."
        )
    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            return ValidationResult.failure(f"questions[{index}] must be an object.")
        question = item.get("question")
        header = item.get("header")
        options = item.get("options")
        if not isinstance(question, str) or not question.strip():
            return ValidationResult.failure(
                f"questions[{index}].question must be a non-empty string."
            )
        if not isinstance(header, str) or not header.strip():
            return ValidationResult.failure(
                f"questions[{index}].header must be a non-empty string."
            )
        if len(header) > MAX_HEADER_LEN:
            return ValidationResult.failure(
                f"questions[{index}].header must be at most {MAX_HEADER_LEN} characters."
            )
        if not isinstance(options, list) or len(options) < MIN_OPTIONS:
            return ValidationResult.failure(
                f"questions[{index}].options must have at least {MIN_OPTIONS} entries."
            )
        if len(options) > MAX_OPTIONS:
            return ValidationResult.failure(
                f"questions[{index}].options must have at most {MAX_OPTIONS} entries."
            )
        for opt_index, option in enumerate(options):
            if not isinstance(option, dict):
                return ValidationResult.failure(
                    f"questions[{index}].options[{opt_index}] must be an object."
                )
            label = option.get("label")
            if not isinstance(label, str) or not label.strip():
                return ValidationResult.failure(
                    f"questions[{index}].options[{opt_index}].label must be a non-empty string."
                )
    return ValidationResult.success()


def _classify_input(
    tool_input: dict[str, Any],
    runtime: ToolRuntime,
) -> ToolCallClassification:
    return ToolCallClassification(
        # Asking the user is a read-only request that requires user
        # interaction; we mark it as not concurrency_safe so the executor
        # serializes it.
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=False,
        targets=(
            ToolTarget(
                kind="session_state",
                operation="user_interaction",
                value="ask_user_question",
            ),
        ),
        permission_subject="ask_user_question",
    )