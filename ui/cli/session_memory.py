"""用于会话记忆适配器的向后兼容 CLI 导入路径。

具体实现已移至 application.runtime 模块，以便应用层在无需导入界面层的情况下进行装配。
现有的 CLI 调用方可继续在此导入。
"""

from __future__ import annotations

from application.runtime import BackgroundSessionMemoryExtractor

__all__ = ["BackgroundSessionMemoryExtractor"]
