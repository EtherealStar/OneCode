"""单个 Agent 运行时会话的可变状态。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
import uuid

from core.transitions import TransitionReason
from services.model.types import ModelUsage


class PermissionMode(StrEnum):
    """运行时的当前权限模式。

    计划模式是一等公民模式，硬性限制工具的可见性与执行。
    故意不通过 RuntimeState.metadata 编码，以便权限系统、注册表、
    附件投影器和 CLI 共享单一的结构化事实来源。
    """

    DEFAULT = "default"
    PLAN = "plan"


@dataclass
class PlanState:
    """计划模式生命周期的结构化状态。

    此对象替代了之前临时的 metadata["plan_file_path"]、
    metadata["permission_mode"] 风格标志。它承载了运行时重新进入、
    在不同模式间流转以及退出计划模式所需的全部信息，无需查询 metadata 字典。
    """

    pre_plan_mode: PermissionMode | None = None
    has_exited_plan_mode: bool = False
    needs_plan_mode_attachment: bool = False
    needs_plan_mode_exit_attachment: bool = False
    plan_slug: str | None = None
    parent_session_id: str | None = None

    def reset(self) -> None:
        """清空所有计划模式状态，同时保留会话级配置。"""

        self.pre_plan_mode = None
        self.has_exited_plan_mode = False
        self.needs_plan_mode_attachment = False
        self.needs_plan_mode_exit_attachment = False
        self.plan_slug = None
        self.parent_session_id = None


@dataclass
class RuntimeState:
    usage: ModelUsage = field(default_factory=ModelUsage)
    turn_count: int = 0
    max_turns: int | None = None
    has_attempted_reactive_compact: bool = False
    has_escalated_max_output_tokens: bool = False
    max_output_recovery_count: int = 0
    last_transition: TransitionReason | None = None
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    # 一等权限模式。计划模式替代了之前 metadata["permission_mode"] 的隐式协议。
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    # 结构化计划模式状态。工具、权限和附件投影器均读取此对象，而非直接操作 metadata。
    plan: PlanState = field(default_factory=PlanState)
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_usage(self, usage: ModelUsage) -> None:
        self.usage.add(usage)

    def set_transition(self, transition: TransitionReason) -> None:
        self.last_transition = transition

    def is_plan_mode(self) -> bool:
        """返回运行时当前是否处于计划模式。"""

        return self.permission_mode == PermissionMode.PLAN

    def start_new_session(self) -> str:
        """开启新的运行时会话。

        用于未来 /clear 这类清空当前对话的入口。该方法会生成新的
        session UUID，并重置和当前消息链相关的运行时计数、恢复状态与
        metadata；max_turns 代表运行时配置，因此不会被重置。None
        表示当前 runtime 不设置轮数上限。
        """

        self.session_id = str(uuid.uuid4())
        self.usage = ModelUsage()
        self.turn_count = 0
        self.has_attempted_reactive_compact = False
        self.has_escalated_max_output_tokens = False
        self.max_output_recovery_count = 0
        self.last_transition = None
        self.permission_mode = PermissionMode.DEFAULT
        self.plan.reset()
        self.metadata.clear()
        # model_turn_counter 由 core/loop.py 在每次模型调用
        # 时自增，这里不需要清零：它原本就在 metadata 里，会被
        # metadata.clear() 一并清除，确保新 session 的 checkpoint
        # 归属 id 从 1 重新开始。
        return self.session_id