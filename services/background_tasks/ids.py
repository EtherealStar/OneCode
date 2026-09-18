"""后台任务 ID 生成。"""

from __future__ import annotations

import secrets

from services.background_tasks.types import BackgroundTaskType

_PREFIXES: dict[BackgroundTaskType, str] = {
    "local_bash": "b_",
    "local_agent": "a_",
    "dream": "d_",
}


def generate_background_task_id(task_type: BackgroundTaskType) -> str:
    """返回带有稳定类型前缀的短随机 ID。"""

    return f"{_PREFIXES[task_type]}{secrets.token_hex(4)}"
