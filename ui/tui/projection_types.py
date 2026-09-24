"""会话投影的表现层值类型。

它们仅承载视图渲染所需的数据：稳定的标识符、有序分片、工具执行状态、结果预览以及详情引用。
绝不暴露模型供应商底层协议格式、Rich 可渲染对象或 Textual 控件。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from application.types import DetailRef

MESSAGE_ROLE_USER = "user"
MESSAGE_ROLE_ASSISTANT = "assistant"
MESSAGE_ROLE_SYSTEM = "system"

LIFECYCLE_DRAFT = "draft"
LIFECYCLE_COMPLETED = "completed"
LIFECYCLE_FAILED = "failed"

PART_TEXT = "text"
PART_TOOL = "tool"
PART_ATTACHMENT = "attachment"


@dataclass(frozen=True)
class UiPart:
    """消息的一个有序分片。"""

    kind: str
    #: 文本正文（``text``）或附件摘要（``attachment``）。
    content: str = ""
    #: 附件元数据，针对 ``attachment`` 分片存在。
    attachment_type: str | None = None
    #: 工具标识，针对 ``tool`` 分片存在。
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_input: Mapping[str, Any] = field(default_factory=dict)
    status: str | None = None
    is_error: bool = False
    progress_text: str = ""
    result_preview: str | None = None
    detail_ref: DetailRef | None = None
    detail_loaded: bool = False
    detail_error: str | None = None


@dataclass(frozen=True)
class UiMessage:
    """具有稳定消息标识、有序分片和生命周期的消息结构。"""

    message_id: str
    role: str
    parts: tuple[UiPart, ...] = ()
    lifecycle: str = LIFECYCLE_COMPLETED


@dataclass(frozen=True)
class ViewChange:
    """交付给视图的语义化变更集。

    其不包含任何高度、ANSI 转义序列、滚动偏移量或计时器：视图自行负责如何合并刷新。
    ``resync_required`` 告知调用方检测到序号空洞，必须获取完整快照而不是盲目猜测。
    """

    updated_ids: tuple[str, ...] = ()
    deleted_ids: tuple[str, ...] = ()
    structure_changed: bool = False
    queue_changed: bool = False
    status_changed: bool = False
    interaction_changed: bool = False
    usage_changed: bool = False
    run_changed: bool = False
    reset: bool = False
    resync_required: bool = False
    immediate: bool = False

    @property
    def empty(self) -> bool:
        return not (
            self.updated_ids
            or self.deleted_ids
            or self.structure_changed
            or self.queue_changed
            or self.status_changed
            or self.interaction_changed
            or self.usage_changed
            or self.run_changed
            or self.reset
            or self.resync_required
        )

    @classmethod
    def none(cls) -> ViewChange:
        return cls()


__all__ = [
    "LIFECYCLE_COMPLETED",
    "LIFECYCLE_DRAFT",
    "LIFECYCLE_FAILED",
    "MESSAGE_ROLE_ASSISTANT",
    "MESSAGE_ROLE_SYSTEM",
    "MESSAGE_ROLE_USER",
    "PART_ATTACHMENT",
    "PART_TEXT",
    "PART_TOOL",
    "UiMessage",
    "UiPart",
    "ViewChange",
]
