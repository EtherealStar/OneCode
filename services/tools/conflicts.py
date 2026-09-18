"""用于工具执行分批的基于目标的冲突检测。

计划模式需要支持感知冲突的并发：两个探索 agent 读取不同的文件可以并行运行，但读取相同文件或父目录时必须将其串行化。classification.concurrency_safe 只是每个工具的单个布尔值，无法描述两个调用之间的两两冲突。本模块将这种两两冲突编码为作用于两个就绪调用的 ToolTarget 元组上的函数。

规则：

- 不同 kind（file、directory、session_state、command、external_service）的目标是独立的，除非其中一个是包含另一个的目录。
- 对路径 P 的写入与在 P 上或包含 P 的任何目录上的任何其他读写冲突。
- 对同一文件或目录的两个读取操作不冲突。
- 目录列出操作与目录内部的写入操作冲突。
- session_state、command、external_service 类型的目标被视为互斥，并发调用无法证明它们不会相互覆盖，因此串行化处理。
- 具有空目标列表的两个调用默认均视为“互斥”，因为没有信息能够证明它们彼此独立。
"""

from __future__ import annotations

from pathlib import Path

from services.tools.types import ToolCallClassification, ToolTarget


def targets_conflict(
    left: tuple[ToolTarget, ...],
    right: tuple[ToolTarget, ...],
) -> bool:
    """当两个目标元组无法安全地并行运行时返回 True。"""

    if not left and not right:
        # 两个未声明任何目标的不透明调用。原始的
        # concurrency_safe 标志已经控制了进入此批次的权限，因此
        # 我们将零目标调用视为相互独立：执行器的外层预检
        # 和描述符的分类是事实来源，而非目标重叠。
        return False
    if not left or not right:
        # 至少有一侧没有目标，而另一侧具有明确的目标。
        # 在没有声明目标的情况下，我们无法推断是否存在重叠，
        # 因此为安全起见回退到串行化。
        return True
    for l_target in left:
        for r_target in right:
            if _pair_conflicts(l_target, r_target):
                return True
    return False


def classifications_conflict(
    left: ToolCallClassification,
    right: ToolCallClassification,
) -> bool:
    """两个完整分类的便捷包装函数。"""

    return targets_conflict(left.targets, right.targets)


def build_conflict_batches(
    items: list[tuple[ToolCallClassification, int]],
) -> list[list[int]]:
    """将索引贪心划分为具有冲突感知的批次。

    items 是 (classification, original_index) 元组的列表。
    我们输出批次，使得在每个批次内任意一对调用均不冲突。
    按设计这是贪心的（因而并非最优解），但它快速、确定，
    并且符合执行器的需求：生成可安全运行的调用组流，而不是解决 NP 难调度问题。
    """

    batches: list[list[int]] = []
    current: list[int] = []
    current_classifications: list[ToolCallClassification] = []
    for classification, index in items:
        if not any(
            classifications_conflict(classification, other) for other in current_classifications
        ):
            current.append(index)
            current_classifications.append(classification)
            continue
        batches.append(current)
        current = [index]
        current_classifications = [classification]
    if current:
        batches.append(current)
    return batches


def _pair_conflicts(left: ToolTarget, right: ToolTarget) -> bool:
    if left.kind != right.kind:
        # 跨类型：仅当其中一个是包含另一个的目录时才冲突。
        # 原则上 session_state、command、external_service 等互斥。
        if left.kind == "directory" and right.kind == "file":
            return _path_contains(left, right)
        if right.kind == "directory" and left.kind == "file":
            return _path_contains(right, left)
        return False

    # 相同类型。
    if left.kind == "file":
        return _file_pair_conflicts(left, right)
    if left.kind == "directory":
        return _directory_pair_conflicts(left, right)
    if left.kind == "session_state":
        # 当涉及不同键时，两个 session_state 写入是独立的；
        # 相同的键必须串行化。
        return left.value == right.value
    # command 和 external_service 调用无法证明独立性，
    # 因此默认串行化。
    return True


def _file_pair_conflicts(left: ToolTarget, right: ToolTarget) -> bool:
    if left.value and right.value and _same_path(left.value, right.value):
        # 相同文件：任意一侧的任何写入或删除操作均与另一侧冲突。
        # 读读操作有意被允许：执行器的预检（以及描述符的 concurrency_safe 标志）
        # 已经决定了读取处理器是否可以安全地并行运行。
        if _is_write(left) or _is_write(right):
            return True
        return False
    # 文件 A 位于目录 B 内（或反之）：对 A 的读取意味着父目录列表的存在，
    # 且对目录的写入可能会在另一个调用执行期间重命名或删除 A。
    left_path = Path(left.value)
    right_path = Path(right.value)
    if _path_contains_str(left.value, right_path.parent):
        return True
    if _path_contains_str(right.value, left_path.parent):
        return True
    return False


def _directory_pair_conflicts(left: ToolTarget, right: ToolTarget) -> bool:
    if _same_path(left.value, right.value):
        # 对同一目录的两次列表操作是只读的，可以并行运行；
        # 调用方已经过滤掉了写入操作。
        return _is_write(left) or _is_write(right)
    return _path_contains_str(left.value, right.value) or _path_contains_str(
        right.value, left.value
    )


def _is_write(target: ToolTarget) -> bool:
    return target.operation in {"write", "delete"}


def _same_path(a: str, b: str) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a) == Path(b)


def _path_contains(directory: ToolTarget, file: ToolTarget) -> bool:
    return _path_contains_str(directory.value, file.value)


def _path_contains_str(directory: str | Path, file: str | Path) -> bool:
    directory_path = Path(directory)
    file_path = Path(file)
    try:
        file_path.resolve().relative_to(directory_path.resolve())
        return True
    except ValueError:
        return False
    except OSError:
        return False