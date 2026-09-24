"""任务列表 ID 解析。"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.runtime_state import RuntimeState


_SAFE_TASK_LIST_ID = re.compile(r"[^A-Za-z0-9._-]+")


def resolve_task_list_id(
    state: RuntimeState,
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    """解析并记忆此运行时的任务列表 ID。

    环境变量优先级最高，以便独立的运行时可以显式共享同一个任务图。
    运行时元数据次之，使子 agent 无需依赖进程级环境变量即可继承父级的任务图。
    """

    source = os.environ if env is None else env
    raw = source.get("ONECODE_TASK_LIST_ID")
    if not raw:
        metadata_value = state.metadata.get("task_list_id")
        if isinstance(metadata_value, str) and metadata_value:
            raw = metadata_value
    if not raw:
        parent_value = state.metadata.get("parent_task_list_id")
        if isinstance(parent_value, str) and parent_value:
            raw = parent_value
    if not raw:
        raw = state.session_id

    task_list_id = _sanitize_task_list_id(raw)
    state.metadata["task_list_id"] = task_list_id
    return task_list_id


def _sanitize_task_list_id(value: str) -> str:
    cleaned = _SAFE_TASK_LIST_ID.sub("_", value.strip())
    cleaned = cleaned.strip("._-")
    return cleaned or "default"
