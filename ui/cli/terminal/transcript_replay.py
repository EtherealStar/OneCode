"""将恢复的消息链重放至主静态区域。

恢复会话时，其历史消息必须以与原始会话完全相同的方式展示在终端回滚历史中。
本模块是遍历已恢复消息链并通过 ui.cli.terminal.static_output 中的常规静态输出渲染器
重新发送的唯一步骤。

设计约束：

- 无专门的恢复摘要格式。用户行复用反色高亮的 print_user_submitted，
  assistant 回复复用 print_assistant_markdown，工具结果复用 print_tool_result。
  随着常规渲染路径的演进，恢复功能自动跟进。
- 本函数仅重放至静态区域。它不变更 MessageStore，不执行工具，不调用模型供应商，
  也不记录追踪。它消费已经恢复的消息字典。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from services.tools.types import ToolExecutionResult
from ui.cli.terminal.static_output import (
    print_assistant_markdown,
    print_tool_result,
    print_user_submitted,
)


def replay_messages_to_static(
    messages: Iterable[dict[str, Any]],
    *,
    brightness: str,
    workspace: Path | None = None,
) -> None:
    """将已恢复的消息重放至静态区域（回滚历史）。

    消息按顺序输出。每个角色路由至实时会话所使用的同一静态输出函数，
    确保恢复的历史记录在视觉上与正常会话完全一致。
    """

    for message in messages:
        role = message.get("role")
        if role == "user":
            _replay_user(message, brightness=brightness)
        elif role == "assistant":
            _replay_assistant(message)
        elif role == "tool_result":
            _replay_tool_result(message, workspace=workspace)
        # attachment 及未知角色有意跳过：实时主屏幕没有为其提供稳定的静态渲染，因此恢复过程不得凭空捏造。


def _replay_user(message: dict[str, Any], *, brightness: str) -> None:
    text = _message_text(message.get("content"))
    if text:
        print_user_submitted(text, brightness=brightness)


def _replay_assistant(message: dict[str, Any]) -> None:
    text = _message_text(message.get("content"))
    # 仅携带工具调用（无可见文本）的 assistant 消息在此处不产生任何输出，与实时回滚历史保持一致（不打印虚假的 assistant: <tool call> 行）。
    if text:
        print_assistant_markdown(text)


def _replay_tool_result(message: dict[str, Any], *, workspace: Path | None) -> None:
    metadata = message.get("metadata")
    result = ToolExecutionResult(
        tool_call_id=str(message.get("tool_call_id") or ""),
        tool_name=str(message.get("tool_name") or "unknown_tool"),
        content=_message_text(message.get("content")),
        is_error=message.get("is_error") is True,
        metadata=metadata if isinstance(metadata, dict) else {},
    )
    print_tool_result(result, call_id=result.tool_call_id, workspace=workspace)


def _message_text(content: Any) -> str:
    """从消息 content 字段中提取可显示的文本。

    内容通常为普通字符串，但也可以是内容块列表（如多模态）。
    我们拼接每个块的文本并忽略非文本部分。
    """

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


__all__ = ["replay_messages_to_static"]
