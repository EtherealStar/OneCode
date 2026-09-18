"""用于会话恢复辅助函数的向后兼容 CLI 导入路径。

具体实现已移至 application.sessions 模块，由应用层负责会话恢复业务。
现有的 CLI 调用方可继续在此导入。
"""

from __future__ import annotations

from application.sessions import (
    SessionSummary,
    list_session_summaries,
    resolve_resume_target,
    restore_runtime_from_target,
    restore_session_state,
    summarize_session,
)

__all__ = [
    "SessionSummary",
    "list_session_summaries",
    "resolve_resume_target",
    "restore_runtime_from_target",
    "restore_session_state",
    "summarize_session",
]
