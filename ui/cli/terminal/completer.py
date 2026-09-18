"""将 ui.cli.suggestions.suggestions_for 适配到 prompt_toolkit 的补全适配器。

prompt_toolkit 的 prompt_toolkit.completion.Completer 是内联模型的正确抽象：
它让框架通过原生 Buffer.complete_state 掌控补全菜单的渲染以及上下方向键导航，
而我们只需将现有的 ui.cli.suggestions.SuggestionItem 对象转换为
prompt_toolkit.completion.Completion 对象。

Enter 与 Tab 语义（执行计划 M3）在 ui.cli.terminal.prompt_session 中通过直接读取
buffer.complete_state 实现；本模块只需计算正确的 start_position，
以确保被采纳的补全项替换输入切片的正确范围。
"""

from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from ui.cli.suggestions import SuggestionItem, suggestions_for
from ui.cli.types import CliRuntime


class InlineCompleter(Completer):
    """由 suggestions_for 提供支持的 prompt_toolkit 补全器。"""

    def __init__(self, runtime: CliRuntime | None) -> None:
        self._runtime = runtime
        # 暴露用于测试和实时状态行；反映最近一次 suggestions_for 的结果。
        self._last_items: tuple[SuggestionItem, ...] = ()

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        runtime = self._runtime
        if runtime is None:
            self._last_items = ()
            return
        text = document.text
        cursor = document.cursor_position
        items = tuple(suggestions_for(runtime, text, cursor))
        self._last_items = items
        for item in items:
            completion = Completion(
                item.replacement,
                start_position=_start_position(text, cursor, item),
                display=item.display,
                display_meta=item.description,
                style="class:completion",
                selected_style="class:completion-selected",
            )
            setattr(completion, "_suggestion_item", item)
            yield completion

    @property
    def last_items(self) -> tuple[SuggestionItem, ...]:
        """最近计算出的建议项的只读视图（用于测试）。"""

        return self._last_items


def _start_position(text: str, cursor: int, item: SuggestionItem) -> int:
    """计算被采纳的补全项应替换光标前多少个字符。

    suggestions_for 会针对斜杠命令和 @file 提及返回候选完整字符串，
    因此替换操作必须先删除光标下的部分词元。
    """

    before = text[:cursor]
    if item.kind in {"command", "session"}:
        # 替换迄今为止键入的整个命令或会话词元。
        if before.startswith("/"):
            return -cursor
        return 0
    if item.kind in {"file", "directory"}:
        at_index = before.rfind("@")
        if at_index < 0:
            return 0
        # 从紧随 @ 之后的位置替换至光标处。替换值不包含 @，
        # 因此我们保留原处的 @，仅替换路径片段。
        return (at_index + 1) - cursor
    return 0
