"""旧导入路径的无状态兼容导出；生产代码只挂载 CompletionOverlay。"""

from .completion import CompletionOverlay

SlashCompletionOverlay = CompletionOverlay

__all__ = ["SlashCompletionOverlay"]
