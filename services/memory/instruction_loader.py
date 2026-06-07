"""Load layered OneCode instruction memory files."""

from __future__ import annotations

from dataclasses import dataclass
import fnmatch
from hashlib import sha256
from pathlib import Path
from typing import Any

from core.runtime_state import RuntimeState
from infrastructure.filesystem.paths import resolve_path
from services.memory.frontmatter import (
    clean_string,
    split_frontmatter,
    string_list,
    strip_html_comments,
)
from services.memory.types import InstructionMemoryFile, InstructionMemoryResult
from services.observability import TraceRecorder

_TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".rst"}
_MAX_INCLUDE_DEPTH = 5


@dataclass(frozen=True)
class _LoadRoot:
    layer: str
    base_dir: Path
    file_path: Path
    rule_base_dir: Path | None = None


class InstructionMemoryLoader:
    """Load ONECODE.md, rules, local overrides, and @include references."""

    def __init__(
        self,
        workspace: Path | str,
        *,
        home: Path | str | None = None,
        trace_recorder: TraceRecorder | None = None,
    ) -> None:
        self.workspace = resolve_path(Path(workspace))
        self.home = resolve_path(Path(home).expanduser()) if home is not None else Path.home().resolve()
        self.onecode_home = self.home / ".onecode"
        self.trace_recorder = trace_recorder or TraceRecorder.noop()

    def load(
        self,
        state: RuntimeState,
        cwd: Path | str,
        target_paths: tuple[Path | str, ...] = (),
    ) -> InstructionMemoryResult:
        cwd_path = _clamp_to_workspace(resolve_path(Path(cwd)), self.workspace)
        targets = tuple(_relative_target(path, self.workspace) for path in target_paths)
        targets += tuple(_target_paths_from_state(state, self.workspace))
        target_texts = tuple(dict.fromkeys(value for value in targets if value))
        warnings: list[str] = []
        loaded: list[InstructionMemoryFile] = []
        seen: set[Path] = set()
        for root in self._candidate_roots(cwd_path):
            if not root.file_path.exists() or not root.file_path.is_file():
                continue
            file = self._load_file(
                root.file_path,
                layer=root.layer,
                base_dir=root.base_dir,
                rule_base_dir=root.rule_base_dir,
                target_paths=target_texts,
                seen=seen,
                depth=0,
                warnings=warnings,
            )
            if file is not None:
                loaded.append(file)
        rendered = "\n\n".join(_format_file(file) for file in loaded if file.content.strip())
        return InstructionMemoryResult(
            files=tuple(loaded),
            rendered_text=rendered,
            fingerprint=_fingerprint(
                *(f"{file.path}:{file.globs}:{file.content}" for file in loaded),
                "|".join(target_texts),
            ),
            warnings=tuple(warnings),
        )

    def _candidate_roots(self, cwd: Path) -> tuple[_LoadRoot, ...]:
        roots: list[_LoadRoot] = []
        roots.extend(self._user_roots())
        for directory in _workspace_chain(self.workspace, cwd):
            roots.append(
                _LoadRoot(
                    "project",
                    self.workspace,
                    directory / "ONECODE.md",
                    rule_base_dir=directory,
                )
            )
            roots.append(
                _LoadRoot(
                    "project",
                    self.workspace,
                    directory / ".onecode" / "ONECODE.md",
                    rule_base_dir=directory,
                )
            )
            for path in sorted((directory / ".onecode" / "rules").glob("*.md")):
                roots.append(
                    _LoadRoot(
                        "project",
                        self.workspace,
                        path,
                        rule_base_dir=directory,
                    )
                )
        for directory in _workspace_chain(self.workspace, cwd):
            roots.append(
                _LoadRoot(
                    "local",
                    self.workspace,
                    directory / "ONECODE.local.md",
                    rule_base_dir=directory,
                )
            )
        return tuple(roots)

    def _user_roots(self) -> list[_LoadRoot]:
        roots = [_LoadRoot("user", self.onecode_home, self.onecode_home / "ONECODE.md")]
        rules_dir = self.onecode_home / "rules"
        if rules_dir.exists():
            roots.extend(
                _LoadRoot("user", self.onecode_home, path, rule_base_dir=self.workspace)
                for path in sorted(rules_dir.glob("*.md"))
            )
        return roots

    def _load_file(
        self,
        path: Path,
        *,
        layer: str,
        base_dir: Path,
        rule_base_dir: Path | None,
        target_paths: tuple[str, ...],
        seen: set[Path],
        depth: int,
        warnings: list[str],
        parent: Path | None = None,
    ) -> InstructionMemoryFile | None:
        path = resolve_path(path)
        if path in seen:
            warnings.append(f"Skipped repeated instruction include: {path}")
            return None
        if not _is_inside(path, base_dir):
            warnings.append(f"Skipped instruction outside allowed root: {path}")
            return None
        if path.suffix.lower() not in _TEXT_SUFFIXES:
            warnings.append(f"Skipped non-text instruction include: {path}")
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            warnings.append(f"Skipped unreadable instruction file {path}: {type(exc).__name__}")
            return None
        metadata, body = split_frontmatter(raw)
        globs = string_list(metadata.get("paths"))
        if globs and not _matches_any_target(
            globs,
            target_paths,
            base_dir=rule_base_dir or base_dir,
            workspace=self.workspace,
            user_rule=layer == "user",
        ):
            return None
        seen.add(path)
        includes: list[str] = []
        body_without_includes: list[str] = []
        for line in body.splitlines():
            include_target = _include_target(line)
            if include_target is None:
                body_without_includes.append(line)
            else:
                includes.append(include_target)
        rendered_parts: list[str] = []
        for include in includes:
            if depth >= _MAX_INCLUDE_DEPTH:
                warnings.append(f"Skipped include beyond depth {_MAX_INCLUDE_DEPTH}: {include}")
                continue
            child_path = (path.parent / include).resolve()
            child = self._load_file(
                child_path,
                layer=layer,
                base_dir=base_dir,
                rule_base_dir=rule_base_dir,
                target_paths=target_paths,
                seen=seen,
                depth=depth + 1,
                warnings=warnings,
                parent=path,
            )
            if child is not None:
                rendered_parts.append(child.content)
        transformed = strip_html_comments("\n".join(body_without_includes)).strip()
        rendered_parts.append(transformed)
        return InstructionMemoryFile(
            path=path,
            source_layer=layer,
            content="\n\n".join(part for part in rendered_parts if part.strip()).strip(),
            globs=globs,
            parent=parent,
            transformed=transformed != body.strip() or bool(includes),
            load_reason=clean_string(metadata.get("description")) or "loaded",
        )


def _format_file(file: InstructionMemoryFile) -> str:
    return f"## {file.source_layer}: {file.path}\n{file.content.strip()}"


def _include_target(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("@"):
        return None
    target = stripped[1:].strip()
    if not target.startswith(("./", "../")):
        return None
    return target


def _workspace_chain(workspace: Path, cwd: Path) -> tuple[Path, ...]:
    try:
        relative = cwd.relative_to(workspace)
    except ValueError:
        return (workspace,)
    directories = [workspace]
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_dir() or not current.suffix:
            directories.append(current)
    return tuple(dict.fromkeys(directories))


def _clamp_to_workspace(path: Path, workspace: Path) -> Path:
    return path if _is_inside(path, workspace) else workspace


def _is_inside(path: Path, base: Path) -> bool:
    try:
        resolve_path(path).relative_to(resolve_path(base))
    except ValueError:
        return False
    return True


def _relative_target(path: Path | str, workspace: Path) -> str:
    target = resolve_path(Path(path))
    try:
        return target.relative_to(workspace).as_posix()
    except ValueError:
        return target.as_posix()


def _target_paths_from_state(state: RuntimeState, workspace: Path) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("files_read", "files_changed", "file_attachments"):
        raw = state.metadata.get(key, ())
        if isinstance(raw, (str, bytes)):
            raw_values = (raw,)
        else:
            try:
                raw_values = tuple(raw)
            except TypeError:
                raw_values = (raw,)
        for value in raw_values:
            if str(value).strip():
                values.append(_relative_target(str(value), workspace))
    return tuple(values)


def _matches_any_target(
    globs: tuple[str, ...],
    targets: tuple[str, ...],
    *,
    base_dir: Path,
    workspace: Path,
    user_rule: bool,
) -> bool:
    if not targets:
        return False
    normalized_targets = tuple(target.replace("\\", "/") for target in targets)
    base_prefix = ""
    if not user_rule:
        try:
            relative_base = base_dir.relative_to(workspace)
            base_prefix = "" if str(relative_base) == "." else relative_base.as_posix()
        except ValueError:
            base_prefix = ""
    for glob in globs:
        pattern = glob.replace("\\", "/").lstrip("/")
        if base_prefix and not pattern.startswith(base_prefix + "/"):
            pattern = f"{base_prefix}/{pattern}"
        for target in normalized_targets:
            if _glob_matches(target, pattern):
                return True
    return False


def _glob_matches(value: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(value, pattern):
        return True
    if "/" not in pattern:
        return fnmatch.fnmatchcase(Path(value).name, pattern)
    if pattern.startswith("**/"):
        return fnmatch.fnmatchcase(value, pattern[3:]) or fnmatch.fnmatchcase(value, pattern)
    return False


def _fingerprint(*parts: str) -> str:
    return sha256("\0".join(parts).encode("utf-8")).hexdigest()
