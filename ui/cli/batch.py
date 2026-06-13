"""Non-interactive batch CLI path — stdin line in, streamed stdout out."""

from __future__ import annotations

import asyncio
from pathlib import Path

from services.model.types import ProviderError
from ui.cli import renderer
from ui.cli.app import build_runtime
from ui.cli.input import read_batch_line
from ui.cli.types import CliRuntime


async def run_batch_async(workspace: Path) -> int:
    try:
        runtime = build_runtime(workspace)
    except ProviderError as exc:
        renderer.print_renderable(renderer.render_error(exc.message))
        return 1
    except Exception as exc:
        renderer.print_renderable(renderer.render_error(str(exc)))
        return 1

    try:
        line = read_batch_line()
    except EOFError:
        await _shutdown(runtime)
        return 0

    line = line.strip()
    if not line:
        await _shutdown(runtime)
        return 0

    try:
        print(renderer.render_running())
        attachments = ()
        if runtime.attachment_collector is not None:
            attachments = await runtime.attachment_collector.collect_for_user_turn(
                line,
                runtime.state,
                runtime.message_store.current_messages(),
                is_main_thread=True,
            )
        saw_delta = False
        final_text = ""
        async for event in runtime.loop.stream(line, attachments=attachments):
            if event.type == "assistant_delta":
                if not saw_delta:
                    print("onecode> ", end="", flush=True)
                saw_delta = True
                print(renderer.render_assistant_delta(event.text), end="", flush=True)
            elif event.type == "tool_result" and event.result is not None:
                print(
                    renderer.render_tool_result_summary(
                        event.result,
                        workspace=runtime.workspace,
                    )
                )
            elif event.type == "completed":
                final_text = event.text
        if saw_delta:
            print()
        else:
            print(renderer.render_assistant(final_text))
    except Exception as exc:
        runtime.error_log_recorder.record_error(
            exc,
            source="cli_main_loop",
            attributes={"turn_count": runtime.state.turn_count},
        )
        runtime.error_log_recorder.flush()
        renderer.print_renderable(renderer.render_error(str(exc)))
        await _shutdown(runtime)
        return 1

    await _shutdown(runtime)
    return 0


async def _shutdown(runtime: CliRuntime) -> None:
    runtime.message_store.flush_transcript()
    runtime.trace_recorder.flush()
    runtime.error_log_recorder.flush()
    if runtime.mcp_manager is not None:
        await runtime.mcp_manager.close_all()


def run_batch(workspace: Path) -> int:
    return asyncio.run(run_batch_async(workspace))
