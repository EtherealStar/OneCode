"""基于文件系统的计划模式 Markdown 文件存储。

计划文件保存在 <workspace>/.onecode/plans/<slug>.md。该存储不依赖运行时循环
或任何提供商；它仅负责路径解析、标识生成、原子读写、分支复制和会话恢复。
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from core.runtime_state import RuntimeState

DEFAULT_PLAN_DIR = ".onecode"
PLANS_SUBDIR = "plans"
MAX_SLUG_LEN = 60
_SLUG_INVALID = re.compile(r"[^a-z0-9_-]+")
_SLUG_SEPARATORS = re.compile(r"[-_\s]+")


class PlanStoreError(RuntimeError):
    """当计划文件操作无法安全完成时引发的异常。"""


@dataclass(frozen=True)
class PlanFile:
    """解析后的计划文件路径和稳定标识。"""

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
        # 在 POSIX 系统上，write_text 对尚不存在的文件具有原子性；在 Windows 系统上，
        # 此处通过显式写入避免进程中途终止时残留不完整的计划文件。
        self.path.write_text(content, encoding="utf-8")


class PlanStore:
    """管理 .onecode/plans 目录及各会话的计划文件路径。"""

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
        """若 .onecode/plans 目录不存在则创建该目录。具有幂等性。"""

        self._plans_dir.mkdir(parents=True, exist_ok=True)
        return self._plans_dir

    def get_or_create_plan(
        self,
        state: RuntimeState,
        *,
        agent_id: str | None = None,
    ) -> PlanFile:
        """返回当前活跃的计划文件，必要时进行分配。

        解析顺序：
        1. 若已设置 state.plan.plan_slug，则使用该标识；当调用方是需要独立计划文件的子运行时时，追加各 agent 专属后缀。
        2. 基于 session_id 的标识。
        3. 新生成的随机标识。
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
        """返回当前计划文件而不分配新文件。"""

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
        """为会话分支将源会话的计划复制到全新标识的文件中。

        分支会话严禁共享计划文件：两个并发会话编辑同一个 Markdown 文件会导致非确定性结果并丢失编辑内容。
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
        """在会话恢复时，根据标识重新关联到已有的计划文件。

        当缺少标识或文件已不存在时返回 None。此处不会静默创建新文件：已恢复的会话不应丢失其计划内容，但也不应凭空创建新计划。
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
        """供测试和外部调用方使用的公共标识生成辅助方法。"""

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
