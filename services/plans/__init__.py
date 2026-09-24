"""计划模式服务：文件存储、状态流转、提示词和附件。

计划存储管理 .onecode/plans/ 下的 Markdown 文件。计划状态本身保存在
core.runtime_state.RuntimeState.plan 中；本包属于纯文件系统与提示词层，
因此无需运行中的运行时即可进行独立测试。
"""

from services.plans.injection import build_plan_attachments_for_state
from services.plans.store import PlanStore, PlanStoreError
from services.plans.transitions import (
    consume_plan_mode_attachment,
    consume_plan_mode_exit_attachment,
    enter_plan_mode,
    exit_plan_mode,
    request_plan_mode_attachment,
)

__all__ = [
    "PlanStore",
    "PlanStoreError",
    "build_plan_attachments_for_state",
    "consume_plan_mode_attachment",
    "consume_plan_mode_exit_attachment",
    "enter_plan_mode",
    "exit_plan_mode",
    "request_plan_mode_attachment",
]
