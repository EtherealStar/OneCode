"""目标 Textual TUI 包。

全屏界面构建于 application 的 SessionController 之上。
projection 模块将控制器的历史快照和有序更新转换为单一、可重建的消息树；
视图（M4）负责渲染该消息树。
"""

from __future__ import annotations

__all__: list[str] = []
