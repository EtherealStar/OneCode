"""Target-based conflict detection for tool execution batching.

Plan mode requires conflict-aware concurrency: two explore agents reading
different files may run in parallel, but reading the same file or parent
directory must serialize them. ``classification.concurrency_safe`` is a
single boolean per tool; it cannot describe pairwise conflicts between two
calls. This module encodes that pairwise conflict as a function over the
``ToolTarget`` tuples of two ready calls.

The rules:

- Targets of different ``kind`` (file vs directory vs session_state vs
  command vs external_service) are independent unless one is a directory
  that contains the other.
- A write to path P conflicts with any other read/write on P or on any
  directory containing P.
- Two reads on the same file or directory do NOT conflict.
- Directory listings conflict with writes inside the directory.
- Targets of kind ``session_state``, ``command``, ``external_service`` are
  treated as exclusive — concurrent calls cannot prove they don't trample
  each other, so we serialize.
- Two calls with empty target lists both default to "exclusive" because we
  have no information to prove independence.
"""

from __future__ import annotations

from pathlib import Path

from services.tools.types import ToolCallClassification, ToolTarget


def targets_conflict(
    left: tuple[ToolTarget, ...],
    right: tuple[ToolTarget, ...],
) -> bool:
    """Return True when the two target tuples cannot safely run in parallel."""

    if not left and not right:
        # Two opaque calls without any declared target. The original
        # ``concurrency_safe`` flag already gated entry into this batch, so
        # we treat zero-target calls as independent: the executor's outer
        # preflight and the descriptor's classification are the source of
        # truth, not target overlap.
        return False
    if not left or not right:
        # At least one side has no target, but the other side has explicit
        # targets. Without a declared target we cannot reason about overlap,
        # so we fall back to serialization to be safe.
        return True
    for l_target in left:
        for r_target in right:
            if _pair_conflicts(l_target, r_target):
                return True
    return False


def classifications_conflict(
    left: ToolCallClassification,
    right: ToolCallClassification,
) -> bool:
    """Convenience wrapper for two full classifications."""

    return targets_conflict(left.targets, right.targets)


def build_conflict_batches(
    items: list[tuple[ToolCallClassification, int]],
) -> list[list[int]]:
    """Greedy partition of indices into conflict-aware batches.

    ``items`` is a list of ``(classification, original_index)`` tuples. We
    emit batches such that within each batch every pair is non-conflicting.
    This is greedy by construction (and therefore not optimal), but it is
    fast, deterministic, and matches what the executor needs: a stream of
    safe-to-run groups rather than an NP-hard schedule.
    """

    batches: list[list[int]] = []
    current: list[int] = []
    current_classifications: list[ToolCallClassification] = []
    for classification, index in items:
        if not any(
            classifications_conflict(classification, other) for other in current_classifications
        ):
            current.append(index)
            current_classifications.append(classification)
            continue
        batches.append(current)
        current = [index]
        current_classifications = [classification]
    if current:
        batches.append(current)
    return batches


def _pair_conflicts(left: ToolTarget, right: ToolTarget) -> bool:
    if left.kind != right.kind:
        # Cross-kind: only conflict when one is a directory that contains the
        # other. ``session_state``, ``command``, ``external_service`` etc.
        # are mutually exclusive on principle.
        if left.kind == "directory" and right.kind == "file":
            return _path_contains(left, right)
        if right.kind == "directory" and left.kind == "file":
            return _path_contains(right, left)
        return False

    # Same kind.
    if left.kind == "file":
        return _file_pair_conflicts(left, right)
    if left.kind == "directory":
        return _directory_pair_conflicts(left, right)
    if left.kind == "session_state":
        # Two session_state writes are independent when they touch different
        # keys; the same key must serialise.
        return left.value == right.value
    # ``command`` and ``external_service`` calls cannot prove independence,
    # so we serialize by default.
    return True


def _file_pair_conflicts(left: ToolTarget, right: ToolTarget) -> bool:
    if left.value and right.value and _same_path(left.value, right.value):
        # Same file: any write or delete on either side conflicts with the
        # other side. read-read is intentionally allowed: the executor's
        # preflight (and the descriptor's ``concurrency_safe`` flag) already
        # decides whether the read handler is safe to run in parallel.
        if _is_write(left) or _is_write(right):
            return True
        return False
    # File A inside directory B (or vice versa): the read of A implies
    # existence of the parent directory listing, and a write to the directory
    # can rename or delete A underneath the other call.
    left_path = Path(left.value)
    right_path = Path(right.value)
    if _path_contains_str(left.value, right_path.parent):
        return True
    if _path_contains_str(right.value, left_path.parent):
        return True
    return False


def _directory_pair_conflicts(left: ToolTarget, right: ToolTarget) -> bool:
    if _same_path(left.value, right.value):
        # Two listings of the same directory are read-only and may run in
        # parallel; the caller already filtered out write operations.
        return _is_write(left) or _is_write(right)
    return _path_contains_str(left.value, right.value) or _path_contains_str(
        right.value, left.value
    )


def _is_write(target: ToolTarget) -> bool:
    return target.operation in {"write", "delete"}


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a) == Path(b)


def _path_contains(directory: ToolTarget, file: ToolTarget) -> bool:
    return _path_contains_str(directory.value, file.value)


def _path_contains_str(directory: str | Path, file: str | Path) -> bool:
    directory_path = Path(directory)
    file_path = Path(file)
    try:
        file_path.resolve().relative_to(directory_path.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False