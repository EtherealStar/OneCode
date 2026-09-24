from __future__ import annotations

from dataclasses import dataclass

from miniagent.workspace_references import WorkspacePathCandidate, WorkspacePathKind


@dataclass(frozen=True, slots=True)
class ReferenceToken:
    start: int
    end: int
    identity: str
    display_token: str
    kind: WorkspacePathKind


@dataclass(frozen=True, slots=True)
class ComposerInputSnapshot:
    text: str
    references: tuple[ReferenceToken, ...]
    revision: int


class ComposerReferenceState:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self._references: tuple[ReferenceToken, ...] = ()
        self._revision = 0

    def snapshot(self) -> ComposerInputSnapshot:
        return ComposerInputSnapshot(self._text, self._references, self._revision)

    @property
    def excluded_identities(self) -> frozenset[str]:
        return frozenset(reference.identity for reference in self._references)

    def set_plain_text(self, text: str) -> None:
        self._text = text
        self._references = ()
        self._revision += 1

    def accept(self, start: int, end: int, candidate: WorkspacePathCandidate) -> ComposerInputSnapshot:
        display = candidate.display_path
        token = f'@"{display}"' if " " in display else f"@{display}"
        self.apply_edit(start, end, token)
        reference = ReferenceToken(start, start + len(token), candidate.identity, token, candidate.kind)
        self._references = tuple(sorted((*self._references, reference), key=lambda item: item.start))
        self._assert_valid()
        return self.snapshot()

    def apply_edit(self, start: int, end: int, replacement: str) -> ComposerInputSnapshot:
        if not 0 <= start <= end <= len(self._text):
            raise ValueError("Composer 编辑范围无效")
        start, end = self.expand_range(start, end)
        delta = len(replacement) - (end - start)
        kept: list[ReferenceToken] = []
        for reference in self._references:
            if reference.end <= start:
                kept.append(reference)
            elif reference.start >= end:
                kept.append(ReferenceToken(
                    reference.start + delta, reference.end + delta, reference.identity,
                    reference.display_token, reference.kind,
                ))
        self._text = self._text[:start] + replacement + self._text[end:]
        self._references = tuple(kept)
        self._revision += 1
        self._assert_valid()
        return self.snapshot()

    def reconcile(self, new_text: str) -> ComposerInputSnapshot:
        if new_text == self._text:
            return self.snapshot()
        prefix = 0
        upper = min(len(self._text), len(new_text))
        while prefix < upper and self._text[prefix] == new_text[prefix]:
            prefix += 1
        suffix = 0
        while (
            suffix < len(self._text) - prefix
            and suffix < len(new_text) - prefix
            and self._text[-suffix - 1] == new_text[-suffix - 1]
        ):
            suffix += 1
        old_end = len(self._text) - suffix
        replacement = new_text[prefix:len(new_text) - suffix if suffix else len(new_text)]
        expanded_start, expanded_end = self.expand_range(prefix, old_end)
        if (expanded_start, expanded_end) != (prefix, old_end):
            # 原生 TextArea 若切开 token，本次 Changed 不可安全猜测 identity，恢复旧快照。
            raise ValueError("原生编辑触及 Reference Token 内部")
        return self.apply_edit(prefix, old_end, replacement)

    def expand_range(self, start: int, end: int) -> tuple[int, int]:
        for reference in self._references:
            touched = start < reference.end and end > reference.start
            insertion_inside = start == end and reference.start < start < reference.end
            if touched or insertion_inside:
                start = min(start, reference.start)
                end = max(end, reference.end)
        return start, end

    def snap_cursor(self, offset: int, *, prefer_end: bool = False) -> int:
        for reference in self._references:
            if reference.start < offset < reference.end:
                return reference.end if prefer_end or offset - reference.start >= reference.end - offset else reference.start
        return offset

    def token_ending_at(self, offset: int) -> ReferenceToken | None:
        return next((item for item in self._references if item.end == offset), None)

    def token_starting_at(self, offset: int) -> ReferenceToken | None:
        return next((item for item in self._references if item.start == offset), None)

    def clear(self) -> None:
        self._text = ""
        self._references = ()
        self._revision += 1

    def _assert_valid(self) -> None:
        previous = 0
        for reference in self._references:
            if reference.start < previous or self._text[reference.start:reference.end] != reference.display_token:
                raise RuntimeError("Reference Token 状态与 Composer 文本不一致")
            previous = reference.end


def offset_from_location(text: str, location: tuple[int, int]) -> int:
    row, column = location
    lines = text.split("\n")
    if row < 0 or row >= len(lines) or column < 0 or column > len(lines[row]):
        raise ValueError("TextArea 光标位置无效")
    return sum(len(line) + 1 for line in lines[:row]) + column


def location_from_offset(text: str, offset: int) -> tuple[int, int]:
    if not 0 <= offset <= len(text):
        raise ValueError("Composer offset 无效")
    before = text[:offset]
    return before.count("\n"), len(before.rsplit("\n", 1)[-1])
