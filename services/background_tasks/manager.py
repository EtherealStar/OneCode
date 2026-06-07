"""In-process manager for local background work."""

from __future__ import annotations

import asyncio
import subprocess
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from services.background_tasks.ids import generate_background_task_id
from services.background_tasks.output import background_task_output_path
from services.background_tasks.types import (
    BackgroundTaskState,
    BackgroundTaskType,
    TERMINAL_STATUSES,
)
from services.observability import TraceRecorder


class BackgroundTaskManager:
    def __init__(
        self,
        *,
        workspace: Path | str,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self._trace_recorder = trace_recorder or TraceRecorder.noop()
        self._lock = threading.RLock()
        self._tasks: dict[str, BackgroundTaskState] = {}

    def list_tasks(self) -> tuple[BackgroundTaskState, ...]:
        with self._lock:
            return tuple(
                sorted(self._tasks.values(), key=lambda item: item.start_time)
            )

    def get(self, task_id: str) -> BackgroundTaskState | None:
        with self._lock:
            return self._tasks.get(task_id)

    def start_bash(
        self,
        *,
        command: str,
        description: str,
        state: RuntimeState,
        cwd: Path,
        bash_exe: Path,
        tool_use_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> BackgroundTaskState:
        task = self._register(
            task_type="local_bash",
            description=description,
            state=state,
            tool_use_id=tool_use_id,
            metadata={
                "command": command,
                "cwd": str(cwd),
                "timeout_ms": timeout_ms,
                "bash_exe": str(bash_exe),
            },
        )
        output_path = Path(task.metadata["output_path_abs"])
        try:
            output_handle = output_path.open("ab", buffering=0)
            process = subprocess.Popen(
                [str(bash_exe), "--noprofile", "--norc", "-lc", command],
                cwd=cwd,
                stdout=output_handle,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            self._complete(
                task.id,
                status="failed",
                summary=f'Background command "{command}" failed to start.',
                metadata={"error": "start_failed"},
                notify=True,
            )
            raise
        finally:
            try:
                output_handle.close()  # type: ignore[possibly-undefined]
            except Exception:
                pass

        self._update(task.id, metadata={"process": process})
        thread = threading.Thread(
            target=self._monitor_process,
            args=(task.id, process, command, timeout_ms),
            daemon=True,
        )
        thread.start()
        return self.get(task.id) or task

    def start_agent(
        self,
        *,
        description: str,
        state: RuntimeState,
        run: Callable[[str], Awaitable[dict[str, Any]]],
        tool_use_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> BackgroundTaskState:
        task = self._register(
            task_type="local_agent",
            description=description,
            state=state,
            tool_use_id=tool_use_id,
            metadata=metadata or {},
        )
        asyncio_task = asyncio.create_task(self._run_async_task(task.id, run, notify=True))
        self._update(task.id, metadata={"asyncio_task": asyncio_task})
        return self.get(task.id) or task

    def start_dream(
        self,
        *,
        description: str,
        state: RuntimeState,
        run: Callable[[str], Awaitable[dict[str, Any] | None]],
        metadata: dict[str, Any] | None = None,
    ) -> BackgroundTaskState:
        task = self._register(
            task_type="dream",
            description=description,
            state=state,
            tool_use_id=None,
            metadata=metadata or {},
        )
        asyncio_task = asyncio.create_task(self._run_async_task(task.id, run, notify=False))
        self._update(task.id, metadata={"asyncio_task": asyncio_task})
        return self.get(task.id) or task

    def stop(self, task_id: str) -> BackgroundTaskState | None:
        task = self.get(task_id)
        if task is None:
            return None
        if task.status in TERMINAL_STATUSES:
            return task
        process = task.metadata.get("process")
        if isinstance(process, subprocess.Popen):
            self._stop_process(process)
        asyncio_task = task.metadata.get("asyncio_task")
        if isinstance(asyncio_task, asyncio.Task):
            asyncio_task.cancel()
        self._append_output(task, "[background task stopped]\n")
        return self._complete(
            task_id,
            status="killed",
            summary=f"Background task {task_id} was stopped.",
            metadata={"stopped": True},
            notify=task.type != "dream",
        )

    def drain_notifications(self, state: RuntimeState) -> tuple[dict[str, object], ...]:
        _ = state
        payloads: list[dict[str, object]] = []
        with self._lock:
            for task in list(self._tasks.values()):
                if task.type == "dream" or task.notified or task.status not in TERMINAL_STATUSES:
                    continue
                summary = str(task.metadata.get("summary") or _default_summary(task))
                payloads.append(
                    {
                        "type": "background_task_notification",
                        "task_id": task.id,
                        "task_type": task.type,
                        "status": task.status,
                        "summary": summary,
                        "output_file": task.output_file,
                        "tool_use_id": task.tool_use_id,
                    }
                )
                self._tasks[task.id] = task.with_updates(notified=True)
        return tuple(payloads)

    def _register(
        self,
        *,
        task_type: BackgroundTaskType,
        description: str,
        state: RuntimeState,
        tool_use_id: str | None,
        metadata: dict[str, Any],
    ) -> BackgroundTaskState:
        task_id = generate_background_task_id(task_type)
        output_path = background_task_output_path(
            self.workspace,
            state.session_id,
            task_id,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.touch(exist_ok=True)
        task = BackgroundTaskState(
            id=task_id,
            type=task_type,
            status="running",
            description=description,
            output_file=str(output_path.relative_to(self.workspace)),
            start_time=time.time(),
            tool_use_id=tool_use_id,
            metadata={**metadata, "output_path_abs": str(output_path)},
        )
        with self._lock:
            self._tasks[task_id] = task
        self._trace_recorder.event(
            "background_task_started",
            {
                "task_id": task_id,
                "task_type": task_type,
                "description": description,
            },
        )
        return task

    def _update(
        self,
        task_id: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> BackgroundTaskState | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            updated = task.with_updates(metadata=metadata or {})
            self._tasks[task_id] = updated
            return updated

    def _complete(
        self,
        task_id: str,
        *,
        status: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
        notify: bool,
    ) -> BackgroundTaskState | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.status in TERMINAL_STATUSES:
                return task
            updated = task.with_updates(
                status=status,
                end_time=time.time(),
                notified=not notify,
                metadata={**(metadata or {}), "summary": summary},
            )
            self._tasks[task_id] = updated
        self._trace_recorder.event(
            "background_task_completed",
            {"task_id": task_id, "task_type": updated.type, "status": status},
        )
        return updated

    def _monitor_process(
        self,
        task_id: str,
        process: subprocess.Popen[bytes],
        command: str,
        timeout_ms: int | None,
    ) -> None:
        timed_out = False
        try:
            if timeout_ms is None:
                exit_code = process.wait()
            else:
                try:
                    exit_code = process.wait(timeout=timeout_ms / 1000)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._stop_process(process)
                    exit_code = process.returncode if process.returncode is not None else 124
        except Exception as exc:
            self._complete(
                task_id,
                status="failed",
                summary=f'Background command "{command}" monitor failed: {type(exc).__name__}.',
                metadata={"error_type": type(exc).__name__},
                notify=True,
            )
            return
        status = "completed" if exit_code == 0 and not timed_out else "failed"
        summary = f'Background command "{command}" {status} (exit code {exit_code}).'
        self._complete(
            task_id,
            status=status,
            summary=summary,
            metadata={"exit_code": exit_code, "timed_out": timed_out},
            notify=True,
        )

    async def _run_async_task(
        self,
        task_id: str,
        run: Callable[[str], Awaitable[dict[str, Any] | None]],
        *,
        notify: bool,
    ) -> None:
        try:
            metadata = await run(task_id) or {}
        except asyncio.CancelledError:
            task = self.get(task_id)
            if task is not None and task.status not in TERMINAL_STATUSES:
                self._append_output(task, "[background task cancelled]\n")
                self._complete(
                    task_id,
                    status="killed",
                    summary=f"Background task {task_id} was cancelled.",
                    metadata={"cancelled": True},
                    notify=notify,
                )
            raise
        except Exception as exc:
            task = self.get(task_id)
            if task is not None:
                self._append_output(task, f"[background task failed] {type(exc).__name__}: {exc}\n")
            self._complete(
                task_id,
                status="failed",
                summary=f"Background task {task_id} failed: {type(exc).__name__}.",
                metadata={"error_type": type(exc).__name__, "error_message": str(exc)},
                notify=notify,
            )
            return
        task = self.get(task_id)
        if task is not None:
            final_summary = str(metadata.pop("summary", "") or f"Background task {task_id} completed.")
            self._complete(
                task_id,
                status="completed",
                summary=final_summary,
                metadata=metadata,
                notify=notify,
            )

    def _append_output(self, task: BackgroundTaskState, text: str) -> None:
        output_path = Path(str(task.metadata.get("output_path_abs", "")))
        if not output_path:
            return
        try:
            with output_path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(text)
        except OSError:
            return

    def _stop_process(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2)
            except Exception:
                return


def _default_summary(task: BackgroundTaskState) -> str:
    return f"Background task {task.id} {task.status}."
