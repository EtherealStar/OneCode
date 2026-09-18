"""行内 REPL 的输入队列。

本模块负责 InlineRepl 使用的先进先出（FIFO）队列。
将队列从原始的 deque[str] 升级为带有类型的 QueuedInput 记录队列，
使 REPL 能够分发不同类型的提交（普通提示词 vs 斜杠命令），
而无需在消费端重新分类文本。

职责：

- 保持插入顺序（FIFO）：使用 collections.deque 实现 O(1) 复杂度的 append / popleft；
- 仅接收非空白行（去除前后空白后）；
- 根据行是否以 / 开头将每个条目归类为 prompt 或 slash；
- 暴露只读的 InputQueue.snapshot 视图，供下游组件（状态行、排队预览）渲染，而无需修改队列；
- 分配单调递增的 sequence，使消费者和测试能够感知稳定的顺序，而不依赖于双端队列索引。

唯一消费者仍为 InlineRepl。StreamingSession 内部的运行中轮次输入框也会调用
InputQueue.push，因此该队列是运行中动态区域与空闲期分发循环之间唯一有意保留的共享通道。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Deque, Literal


# 队列可承载的输入类型。prompt 为常规用户轮次；slash 为以 / 开头的行，必须通过命令分发器路由而非走 agent 循环。
QueuedInputKind = Literal["prompt", "slash"]


@dataclass(frozen=True)
class QueuedInput:
    """等待分发的单个排队提交项。

    属性：
        text：字面提交文本（已剥离尾随空白）。原始前导空白也会被丢弃，因为队列仅接收非空白输入。
        kind：prompt 或 slash 之一。slash 条目以 / 开头且必须跳过 agent 循环。
        sequence：单调递增的插入计数器，使消费者能够感知顺序而不依赖于双端队列索引。
        visible：运行中轮次预览是否应当渲染此条目。预留供后续使用（例如不应污染预览的系统插入条目）。
    """

    text: str
    kind: QueuedInputKind
    sequence: int
    visible: bool = True


@dataclass
class InputQueue:
    """等待分发的 QueuedInput 记录的先进先出（FIFO）队列。"""

    _items: Deque[QueuedInput] = field(default_factory=deque)
    _next_sequence: int = 0

    def push(self, line: str) -> QueuedInput | None:
        """追加提交项；返回排队对象，若为空白则返回 None。

        空白行（仅含空白或去除空白后为空）将被静默丢弃。
        斜杠命令保留前导 /；队列不会重新格式化文本。
        """

        normalized = line.rstrip()
        if not normalized.strip():
            return None
        kind: QueuedInputKind = "slash" if normalized.lstrip().startswith("/") else "prompt"
        item = QueuedInput(
            text=normalized,
            kind=kind,
            sequence=self._next_sequence,
        )
        self._next_sequence += 1
        self._items.append(item)
        return item

    def pop(self) -> QueuedInput | None:
        """返回并移除下一个排队的输入项，若为空则返回 None。"""

        if not self._items:
            return None
        return self._items.popleft()

    def snapshot(self) -> tuple[QueuedInput, ...]:
        """排队预览和测试使用的只读快照。

        返回的元组为副本；后续对队列的修改不会影响已生成的快照。
        """

        return tuple(self._items)

    def clear(self) -> None:
        """丢弃所有排队的输入项。在关闭退出时使用。"""

        self._items.clear()

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __iter__(self) -> Iterator[QueuedInput]:
        return iter(tuple(self._items))


__all__ = ["InputQueue", "QueuedInput", "QueuedInputKind"]