from __future__ import annotations

from pathlib import Path

from infrastructure.filesystem.paths import (
    contains_path,
    normalize_path_pattern,
    resolve_path,
    resolve_write_target,
    windows_path,
)
from services.guard import SandboxBoundary, SandboxGuard


def test_contains_path_does_not_match_similar_prefix(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo2 = tmp_path / "repo2"
    child = repo / "a.txt"
    repo.mkdir()
    repo2.mkdir()
    child.write_text("ok", encoding="utf-8")

    assert contains_path(repo, child)
    assert not contains_path(repo, repo2)


def test_windows_path_normalizes_equivalent_drive_forms() -> None:
    assert windows_path("/C:/repo/a.txt", platform="nt") == "C:/repo/a.txt"
    assert windows_path("/c/repo/a.txt", platform="nt") == "C:/repo/a.txt"
    assert windows_path("/cygdrive/c/repo/a.txt", platform="nt") == "C:/repo/a.txt"
    assert windows_path("/mnt/c/repo/a.txt", platform="nt") == "C:/repo/a.txt"


def test_normalize_path_pattern_preserves_wildcard(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    pattern = normalize_path_pattern(repo / "*")

    assert pattern.endswith(f"{Path('repo')}/*") or pattern.endswith(f"{Path('repo')}\\*")


def test_guard_allows_workspace_path(tmp_path: Path) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("ok", encoding="utf-8")
    guard = SandboxGuard(SandboxBoundary(cwd=tmp_path))

    policy = guard.check_path("a.txt", operation="read")

    assert policy.action == "allow"
    assert policy.decision.kind == "inside_workspace"
    assert policy.normalized_path == resolve_path(file_path)


def test_guard_allows_worktree_outside_cwd(tmp_path: Path) -> None:
    worktree = tmp_path / "repo"
    cwd = worktree / "packages" / "app"
    sibling = worktree / "README.md"
    cwd.mkdir(parents=True)
    sibling.write_text("ok", encoding="utf-8")
    guard = SandboxGuard(SandboxBoundary(cwd=cwd, worktree=worktree))

    policy = guard.check_path(sibling, operation="read")

    assert policy.action == "allow"
    assert policy.decision.kind == "inside_worktree"


def test_root_worktree_does_not_allow_arbitrary_paths(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    external = tmp_path / "external.txt"
    cwd.mkdir()
    external.write_text("outside", encoding="utf-8")
    guard = SandboxGuard(SandboxBoundary(cwd=cwd, worktree=Path(Path.cwd().anchor)))

    policy = guard.check_path(external, operation="read")

    assert policy.action == "ask"
    assert policy.decision.kind == "external_directory"


def test_guard_allows_extra_allowed_directory(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    extra = tmp_path / "extra"
    target = extra / "note.txt"
    cwd.mkdir()
    extra.mkdir()
    target.write_text("ok", encoding="utf-8")
    guard = SandboxGuard(SandboxBoundary(cwd=cwd, extra_allowed_dirs=(extra,)))

    policy = guard.check_path(target, operation="read")

    assert policy.action == "allow"
    assert policy.decision.kind == "inside_extra_allowed"


def test_external_directory_generates_parent_pattern(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    external_dir = tmp_path / "outside"
    target = external_dir / "file.txt"
    cwd.mkdir()
    external_dir.mkdir()
    target.write_text("outside", encoding="utf-8")
    guard = SandboxGuard(SandboxBoundary(cwd=cwd))

    policy = guard.check_path(target, operation="read", kind="file")

    assert policy.action == "ask"
    assert policy.decision.kind == "external_directory"
    assert policy.pattern == normalize_path_pattern(external_dir / "*")


def test_denied_pattern_wins_before_workspace_allow(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    target = secret / "token.txt"
    secret.mkdir()
    target.write_text("blocked", encoding="utf-8")
    guard = SandboxGuard(
        SandboxBoundary(cwd=tmp_path, denied_patterns=(str(secret / "*"),))
    )

    policy = guard.check_path(target, operation="read")

    assert policy.action == "deny"
    assert policy.decision.kind == "denied"
    assert policy.to_tool_error()["decision"] == "denied"


def test_relative_denied_pattern_is_resolved_from_boundary_cwd(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    secret = cwd / "secret"
    target = secret / "token.txt"
    secret.mkdir(parents=True)
    target.write_text("blocked", encoding="utf-8")
    guard = SandboxGuard(SandboxBoundary(cwd=cwd, denied_patterns=("secret/*",)))

    policy = guard.check_path(target, operation="read")

    assert policy.action == "deny"
    assert policy.pattern == "secret/*"


def test_missing_write_target_inside_workspace_is_allowed(tmp_path: Path) -> None:
    guard = SandboxGuard(SandboxBoundary(cwd=tmp_path))

    policy = guard.check_write_target("new/child.txt")

    assert policy.action == "allow"
    assert policy.decision.kind == "inside_workspace"
    assert policy.normalized_path == resolve_write_target(
        "new/child.txt",
        base_dir=tmp_path,
    ).target


def test_existing_symlink_to_external_is_not_workspace_if_supported(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "repo"
    outside = tmp_path / "outside"
    target = outside / "secret.txt"
    link = cwd / "link.txt"
    cwd.mkdir()
    outside.mkdir()
    target.write_text("secret", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        return
    guard = SandboxGuard(SandboxBoundary(cwd=cwd))

    policy = guard.check_path(link, operation="read")

    assert policy.action == "ask"
    assert policy.decision.kind == "external_directory"


def test_missing_write_target_through_symlink_parent_is_external(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "repo"
    outside = tmp_path / "outside"
    link_dir = cwd / "linked"
    cwd.mkdir()
    outside.mkdir()
    try:
        link_dir.symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    guard = SandboxGuard(SandboxBoundary(cwd=cwd))

    policy = guard.check_write_target(link_dir / "new.txt")

    assert policy.action == "ask"
    assert policy.decision.kind == "external_directory"
