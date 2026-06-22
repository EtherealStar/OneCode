"""Filesystem-backed store for plan-mode Markdown files.

Plan files live at ``<workspace>/.onecode/plans/<slug>.md``. The store does not
depend on the runtime loop or any provider; it only owns pathing, slug
generation, atomic read/write, fork copy, and resume recovery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
import secrets
import unicodedata

from core.runtime_state import PlanState, RuntimeState

DEFAULT_PLAN_DIR = ".onecode"
PLANS_SUBDIR = "plans"
MAX_SLUG_LEN = 60
_SLUG_INVALID = re.compile(r"[^a-z0-9_-]+")
_SLUG_SEPARATORS = re.compile(r"[-_\s]+")


class PlanStoreError(RuntimeError):
    """Raised when a plan file operation cannot complete safely."""


@dataclass(frozen=True)
class PlanFile:
    """Resolved plan file location and a stable slug."""

    slug: str
    path: Path

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> str:
        if not self.path.is_file():
            return ""
        return self.path.read_text(encoding="utf-8", errors="replace")

    def write(self, content: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # ``write_text`` is atomic on POSIX for files that do not exist; on
        # Windows we explicitly flush + replace to avoid leaving a half-written
        # plan when the process dies mid-write.
        self.path.write_text(content, encoding="utf-8")


class PlanStore:
    """Owns the ``.onecode/plans`` directory and per-session plan file paths."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace.resolve()
        self._plans_dir = self._workspace / DEFAULT_PLAN_DIR / PLANS_SUBDIR

    @property
    def workspace(self) -> Path:
        return self._workspace

    @property
    def plans_dir(self) -> Path:
        return self._plans_dir

    def ensure_layout(self) -> Path:
        """Create ``.onecode/plans`` if missing. Idempotent."""

        self._plans_dir.mkdir(parents=True, exist_ok=True)
        return self._plans_dir

    def get_or_create_plan(
        self,
        state: RuntimeState,
        *,
        agent_id: str | None = None,
    ) -> PlanFile:
        """Return the active plan file, allocating one if needed.

        Resolution order:
        1. ``state.plan.plan_slug`` if set, with a per-agent suffix when the
           caller is a child runtime that needs its own plan file.
        2. ``<session_id>`` based slug.
        3. A freshly generated random slug.
        """

        self.ensure_layout()
        if state.plan.plan_slug:
            slug = self._compose_slug(state.plan.plan_slug, agent_id)
            return PlanFile(slug=slug, path=self._plans_dir / f"{slug}.md")

        if state.session_id:
            slug = self._compose_slug(state.session_id, agent_id)
            state.plan.plan_slug = state.session_id
            return PlanFile(slug=slug, path=self._plans_dir / f"{slug}.md")

        slug = self._compose_slug(_random_slug(), agent_id)
        state.plan.plan_slug = slug
        return PlanFile(slug=slug, path=self._plans_dir / f"{slug}.md")

    def read_plan(
        self,
        state: RuntimeState,
        *,
        agent_id: str | None = None,
    ) -> PlanFile:
        """Return the current plan file without allocating a new one."""

        if state.plan.plan_slug:
            slug = self._compose_slug(state.plan.plan_slug, agent_id)
        elif state.session_id:
            slug = self._compose_slug(state.session_id, agent_id)
        else:
            raise PlanStoreError("Cannot resolve plan: no slug or session id.")
        return PlanFile(slug=slug, path=self._plans_dir / f"{slug}.md")

    def copy_for_fork(
        self,
        source_state: RuntimeState,
        target_state: RuntimeState,
    ) -> PlanFile:
        """Copy the source session's plan to a brand new slug for a fork.

        Forks must not share plan files: two concurrent sessions editing the
        same markdown would produce nondeterministic results and lost edits.
        """

        self.ensure_layout()
        if not source_state.plan.plan_slug:
            raise PlanStoreError("Source plan has no slug; nothing to copy.")
        source_path = self._plans_dir / f"{source_state.plan.plan_slug}.md"
        new_slug = _random_slug()
        target_path = self._plans_dir / f"{new_slug}.md"
        if source_path.is_file():
            content = source_path.read_text(encoding="utf-8", errors="replace")
            target_path.write_text(content, encoding="utf-8")
        target_state.plan.plan_slug = new_slug
        return PlanFile(slug=new_slug, path=target_path)

    def recover_for_resume(
        self,
        state: RuntimeState,
        plan_slug: str | None,
    ) -> PlanFile | None:
        """Re-attach to an existing plan file by slug on session resume.

        Returns ``None`` when the slug is missing or the file no longer exists.
        We do not silently create a new file: a resumed session should not lose
        its plan content, but it also should not invent a fresh plan.
        """

        if not plan_slug:
            return None
        if not self._plans_dir.is_dir():
            return None
        slug = _safe_slug(plan_slug)
        if not slug:
            return None
        path = self._plans_dir / f"{slug}.md"
        if not path.is_file():
            return None
        state.plan.plan_slug = slug
        return PlanFile(slug=slug, path=path)

    @staticmethod
    def slugify(text: str) -> str:
        """Public slug helper for tests and external callers."""

        return _safe_slug(text)

    @staticmethod
    def _compose_slug(base: str, agent_id: str | None) -> str:
        slug = _safe_slug(base)
        if not slug:
            slug = _random_slug()
        if agent_id:
            suffix = _safe_slug(agent_id)
            if suffix:
                slug = f"{slug}-{suffix}"
        return slug[:MAX_SLUG_LEN]


def _safe_slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().strip()
    separated = _SLUG_SEPARATORS.sub("-", lowered)
    cleaned = _SLUG_INVALID.sub("", separated).strip("-")
    return cleaned[:MAX_SLUG_LEN]


def _random_slug() -> str:
    return f"plan-{secrets.token_hex(6)}"
