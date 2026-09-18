"""Non-interactive batch adapter for the SessionController.

stdin line in, streamed plain text out. The batch path shares the same
session contract as the TUI: it submits intent and consumes ordered updates.
It never imports Textual, never starts the interactive REPL, and never enters
an alternate screen.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from application.session import SessionController
from application.types import (
    AssistantDelta,
    InteractionRequested,
    RunCancelled,
    RunCompleted,
    RunFailed,
    SnapshotUpdate,
    ToolUpdate,
)
from services.model.types import ProviderError
from services.permissions import PermissionResponse
from services.questions.types import AnswerRecord, QuestionResponse
from ui.cli import renderer
from ui.cli.app import build_runtime
from ui.cli.input import ConfirmOption, read_batch_line, read_confirm_sync
from ui.cli.permissions import render_permission_request_summary
from ui.cli.types import CliRuntime


class BatchPermissionPrompter:
    """Line-based fallback for non-TTY permission requests.

    The controller normally routes permission requests through the batch
    watcher, which answers them with this same protocol. This class remains
    for runtimes assembled without an interaction coordinator.
    """

    async def request_permission(self, request):
        print(render_permission_request_summary(request))
        print()
        print("Do you want to proceed?")
        options = (
            ConfirmOption("1", "1 yes", aliases=("y", "yes")),
            ConfirmOption("2", "2 session", aliases=("s", "session")),
            ConfirmOption("3", "3 no", aliases=("n", "no", "deny")),
        )
        try:
            choice = await asyncio.to_thread(
                read_confirm_sync,
                "Select [1] yes  [2] session  [3] no: ",
                options,
            )
        except (EOFError, KeyboardInterrupt):
            return PermissionResponse(
                action="deny",
                feedback="Permission prompt was interrupted.",
            )
        if choice == "1":
            return PermissionResponse(action="allow", scope="once")
        if choice == "2":
            return PermissionResponse(action="allow", scope="session")
        return PermissionResponse(
            action="deny",
            feedback="User denied the permission request.",
        )


class BatchUserQuestionPrompter:
    """Auto-accept the first option for each question (CI/batch fallback)."""

    async def ask_questions(self, questions) -> QuestionResponse:
        answers: list[AnswerRecord] = []
        for question in questions:
            if not question.options:
                return QuestionResponse(declined=True, feedback="no_options")
            label = question.options[0].label
            answers.append(
                AnswerRecord(
                    question=question.question,
                    answer=label if not question.multi_select else (label,),
                )
            )
        return QuestionResponse(answers=tuple(answers))


async def run_batch_async(workspace: Path) -> int:
    try:
        runtime = build_runtime(workspace)
    except ProviderError as exc:
        renderer.print_renderable(renderer.render_error(exc.message))
        return 1
    except Exception as exc:
        renderer.print_renderable(renderer.render_error(str(exc)))
        return 1

    controller = SessionController(runtime)
    try:
        try:
            line = read_batch_line()
        except EOFError:
            await controller.close()
            return 0

        line = line.strip()
        if not line:
            await controller.close()
            return 0

        try:
            await controller.start()
            print(renderer.render_running())
            stream = controller.watch()
            await stream.__anext__()
            receipt = await controller.submit(line)
            if receipt.status == "rejected":
                await stream.aclose()
                renderer.print_renderable(
                    renderer.render_error(f"Batch input rejected: {receipt.reason}")
                )
                await controller.close()
                return 1
            failed = await _consume_updates(controller, stream, runtime)
            await stream.aclose()
        except Exception as exc:
            runtime.error_log_recorder.record_error(
                exc,
                source="cli_main_loop",
                attributes={"turn_count": runtime.state.turn_count},
            )
            runtime.error_log_recorder.flush()
            renderer.print_renderable(renderer.render_error(str(exc)))
            await controller.close()
            return 1
    finally:
        if not controller.closed:
            await controller.close()
    return 1 if failed else 0


async def _consume_updates(
    controller: SessionController,
    stream: Any,
    runtime: CliRuntime,
) -> bool:
    """Print updates until the turn reaches a terminal state.

    Returns ``True`` when the batch turn failed and the process should exit
    non-zero.
    """

    saw_delta = False
    final_text = ""
    while True:
        update = await stream.__anext__()
        if isinstance(update, SnapshotUpdate):
            continue
        if isinstance(update, AssistantDelta):
            if not saw_delta:
                print("onecode> ", end="", flush=True)
            saw_delta = True
            print(renderer.render_assistant_delta(update.text), end="", flush=True)
            continue
        if isinstance(update, ToolUpdate):
            if update.result is not None:
                print(
                    renderer.render_tool_result_summary(
                        update.result,
                        workspace=runtime.workspace,
                    )
                )
            continue
        if isinstance(update, InteractionRequested):
            await _answer_interaction(controller, update.request)
            continue
        if isinstance(update, RunCompleted):
            final_text = update.text
            break
        if isinstance(update, RunFailed):
            print()
            renderer.print_renderable(renderer.render_error(update.error))
            return True
        if isinstance(update, RunCancelled):
            print()
            return True
    if saw_delta:
        print()
    else:
        print(renderer.render_assistant(final_text))
    return False


async def _answer_interaction(controller: SessionController, request: Any) -> None:
    kind = request.kind
    if kind == "permission":
        response = await BatchPermissionPrompter().request_permission(request.payload)
        await controller.respond(request.request_id, kind, response)
        return
    if kind == "question":
        response = await BatchUserQuestionPrompter().ask_questions(request.payload)
        await controller.respond(request.request_id, kind, response)
        return
    if kind == "mcp_trust":
        choice = await _read_trust_choice()
        await controller.respond(request.request_id, kind, choice)
        return
    # Unknown/unhandled interaction: cancel it so the turn is not blocked.
    await controller.respond(request.request_id, kind, None)


async def _read_trust_choice() -> str:
    try:
        choice = await asyncio.to_thread(
            read_confirm_sync,
            "Trust this project MCP server?",
            (
                ConfirmOption("trust", "t trust", aliases=("t", "y", "yes")),
                ConfirmOption("skip", "s skip", aliases=("s", "n", "no")),
            ),
        )
    except (EOFError, KeyboardInterrupt):
        return "skip"
    return "trust" if choice == "trust" else "skip"


def run_batch(workspace: Path) -> int:
    return asyncio.run(run_batch_async(workspace))
