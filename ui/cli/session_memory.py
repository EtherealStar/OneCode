"""CLI adapter that schedules session-memory extraction in the background."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.background_tasks import BackgroundTaskManager
from services.compaction import SessionMemoryExtractionService


class BackgroundSessionMemoryExtractor:
    """Schedule session-memory extraction without blocking the active turn."""

    def __init__(
        self,
        extractor: SessionMemoryExtractionService,
        background_task_manager: BackgroundTaskManager,
    ) -> None:
        self.extractor = extractor
        self._background_task_manager = background_task_manager

    async def maybe_extract_after_model_response(
        self,
        messages: tuple[dict[str, Any], ...],
        state: RuntimeState,
        *,
        assistant_message: dict[str, Any],
        tool_calls: tuple[Any, ...],
        usage: Any | None = None,
    ) -> None:
        job = self.extractor.prepare_extraction_job(
            messages,
            state,
            assistant_message=assistant_message,
            tool_calls=tool_calls,
            usage=usage,
        )
        if job is None:
            return

        async def run(task_id: str) -> dict[str, Any]:
            task = self._background_task_manager.get(task_id)
            output_path = (
                Path(str(task.metadata.get("output_path_abs", "")))
                if task is not None
                else None
            )
            if output_path is not None:
                _append_output(output_path, "dream: updating session memory\n")
                _append_output(output_path, f"parent_session_id: {job.parent_session_id}\n")
            try:
                result = await self.extractor.run_extraction_job(job, state)
            except asyncio.CancelledError:
                self.extractor.record_background_cancelled(state)
                raise
            if output_path is not None:
                _append_output(output_path, "dream: completed\n")
                result_session_id = result.get("result_session_id")
                if result_session_id:
                    _append_output(output_path, f"child_session_id: {result_session_id}\n")
            return result

        task = self._background_task_manager.start_dream(
            description="updating session memory",
            state=state,
            run=run,
            metadata={"memory_path": str(self.extractor.store.path)},
        )
        self.extractor.record_background_task(state, task.id)

    async def wait_for_current_extraction(self, state: RuntimeState) -> None:
        await self.extractor.wait_for_current_extraction(state)


def _append_output(path: Path, text: str) -> None:
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
    except OSError:
        return


__all__ = ["BackgroundSessionMemoryExtractor"]
