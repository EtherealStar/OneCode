"""Tests for the target-conflict aware batching in the tool executor."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from core.runtime_state import RuntimeState
from services.tools.conflicts import (
    build_conflict_batches,
    classifications_conflict,
    targets_conflict,
)
from services.tools.executor import RegistryToolExecutor
from services.tools.registry import ToolRegistry
from services.tools.types import (
    ToolCall,
    ToolCallClassification,
    ToolDescriptor,
    ToolExecutionResult,
    ToolRuntime,
    ToolTarget,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Pure conflict helpers
# ---------------------------------------------------------------------------


def _target(kind: str, value: str, operation: str = "read") -> ToolTarget:
    return ToolTarget(kind=kind, operation=operation, value=value)


def test_targets_no_overlap_does_not_conflict() -> None:
    left = (_target("file", "/a/x.py", "read"),)
    right = (_target("file", "/a/y.py", "read"),)
    assert targets_conflict(left, right) is False


def test_targets_same_file_read_read_does_not_conflict() -> None:
    # Two concurrent reads of the same file are explicitly allowed: the
    # conflict helper only encodes the file-system shape; read-read
    # compatibility is the executor's job.
    left = (_target("file", "/a/x.py", "read"),)
    right = (_target("file", "/a/x.py", "read"),)
    assert targets_conflict(left, right) is False


def test_targets_write_read_on_same_file_conflicts() -> None:
    left = (_target("file", "/a/x.py", "write"),)
    right = (_target("file", "/a/x.py", "read"),)
    assert targets_conflict(left, right) is True


def test_targets_write_write_on_same_file_conflicts() -> None:
    left = (_target("file", "/a/x.py", "write"),)
    right = (_target("file", "/a/x.py", "write"),)
    assert targets_conflict(left, right) is True


def test_directory_contains_file_conflicts() -> None:
    left = (_target("directory", "/a", "write"),)
    right = (_target("file", "/a/b/c.py", "write"),)
    assert targets_conflict(left, right) is True


def test_empty_targets_default_to_non_conflict() -> None:
    # When both sides opt out of declaring targets, the original
    # ``concurrency_safe`` flag is the source of truth, not target overlap.
    assert targets_conflict((), ()) is False


def test_session_state_targets_serialise() -> None:
    left = (_target("session_state", "permission_mode"),)
    right = (_target("session_state", "permission_mode"),)
    assert targets_conflict(left, right) is True


def test_build_conflict_batches_separates_overlapping_writes() -> None:
    a = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=True,
        targets=(_target("file", "/a/x.py", "write"),),
    )
    b = ToolCallClassification(
        read_only=True,
        modifies_filesystem=False,
        concurrency_safe=True,
        targets=(_target("file", "/a/y.py", "read"),),
    )
    c = ToolCallClassification(
        read_only=False,
        modifies_filesystem=True,
        concurrency_safe=True,
        targets=(_target("file", "/a/x.py", "write"),),
    )
    batches = build_conflict_batches([(a, 0), (b, 1), (c, 2)])
    # ``a`` and ``b`` can co-run; ``c`` overlaps with ``a`` so it goes into a
    # separate batch. The order of batches depends on the greedy scan, but
    # the contents must separate.
    assert len(batches) == 2
    flat = [index for batch in batches for index in batch]
    assert sorted(flat) == [0, 1, 2]


# ---------------------------------------------------------------------------
# Integration with the executor: parallel + serial batching
# ---------------------------------------------------------------------------


def _make_descriptor(name: str, started: list[str], lock: threading.Lock, sleep_for: float) -> ToolDescriptor:
    def handler(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolExecutionResult:
        with lock:
            started.append(tool_input["call_id"])
        time.sleep(sleep_for)
        return ToolExecutionResult(
            tool_call_id=tool_input["call_id"],
            tool_name=name,
            content=tool_input["call_id"],
        )

    def classify(tool_input: dict[str, Any], runtime: ToolRuntime) -> ToolCallClassification:
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(
                ToolTarget(
                    kind="file",
                    operation="read",
                    value=tool_input["file_path"],
                ),
            ),
        )

    def validate(tool_input: dict[str, Any], runtime: ToolRuntime) -> ValidationResult:
        return ValidationResult.success()

    return ToolDescriptor(
        name=name,
        description="",
        input_schema={"type": "object"},
        handler=handler,
        validate_input=validate,
        classify_input=classify,
    )


def _execute(executor: RegistryToolExecutor, calls: tuple[ToolCall, ...]):
    async def run():
        results = []
        async for update in executor.execute(calls, RuntimeState()):
            if update.type == "result":
                results.append(update.result)
        return results
    return asyncio.run(run())


def test_executor_runs_non_conflicting_calls_in_parallel() -> None:
    started: list[str] = []
    lock = threading.Lock()

    def handler(tool_input, runtime):
        with lock:
            started.append(tool_input["call_id"])
        import time
        time.sleep(0.1)
        return ToolExecutionResult(
            tool_call_id=tool_input["call_id"],
            tool_name="probe",
            content=tool_input["call_id"],
        )

    def classify(tool_input, runtime):
        # Use a session_state target to avoid forcing a real sandbox guard in
        # this unit test; each call_id targets its own key so they don't
        # conflict and the batching path runs them in parallel.
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(
                ToolTarget(
                    kind="session_state",
                    operation="mutate_state",
                    value=tool_input["call_id"],
                ),
            ),
        )

    descriptor = ToolDescriptor(
        name="probe",
        description="",
        input_schema={"type": "object"},
        handler=handler,
        validate_input=lambda tool_input, runtime: ValidationResult.success(),
        classify_input=classify,
    )
    executor = RegistryToolExecutor(ToolRegistry([descriptor]), max_tool_concurrency=4)
    calls = (
        ToolCall(id="a", name="probe", input={"call_id": "a"}),
        ToolCall(id="b", name="probe", input={"call_id": "b"}),
    )
    import time
    t0 = time.perf_counter()
    results = _execute(executor, calls)
    elapsed = time.perf_counter() - t0
    assert sorted(started) == ["a", "b"]
    # Two non-conflicting session_state operations should run in parallel.
    assert elapsed < 0.18
    assert [r.content for r in results] == ["a", "b"]


def test_executor_serialises_conflicting_session_state_targets() -> None:
    """Two calls that both target the same session_state value must serialise.

    This test uses session_state targets (no filesystem guard) so the unit
    test is hermetic, but it still exercises the target-conflict batching
    path that the plan-mode explore-agent dispatch relies on.
    """

    started: list[str] = []
    lock = threading.Lock()

    def handler(tool_input, runtime):
        with lock:
            started.append(tool_input["call_id"])
        import time
        time.sleep(0.05)
        return ToolExecutionResult(
            tool_call_id=tool_input["call_id"],
            tool_name="probe",
            content=tool_input["call_id"],
        )

    def classify(tool_input, runtime):
        return ToolCallClassification(
            read_only=True,
            modifies_filesystem=False,
            concurrency_safe=True,
            targets=(
                ToolTarget(
                    kind="session_state",
                    operation="mutate_state",
                    value="shared",
                ),
            ),
        )

    descriptor = ToolDescriptor(
        name="probe",
        description="",
        input_schema={"type": "object"},
        handler=handler,
        validate_input=lambda tool_input, runtime: ValidationResult.success(),
        classify_input=classify,
    )
    executor = RegistryToolExecutor(ToolRegistry([descriptor]), max_tool_concurrency=4)
    calls = (
        ToolCall(id="a", name="probe", input={"call_id": "a"}),
        ToolCall(id="b", name="probe", input={"call_id": "b"}),
    )
    import time
    t0 = time.perf_counter()
    _execute(executor, calls)
    elapsed = time.perf_counter() - t0
    # session_state is treated as exclusive, so two calls must serialise.
    assert elapsed >= 0.09
