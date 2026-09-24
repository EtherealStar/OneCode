from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from miniagent.workspace_references import WorkspacePathCandidate, WorkspacePathSnapshot, search_snapshot

from .composer_references import ComposerInputSnapshot


@dataclass(frozen=True, slots=True)
class WorkspaceQuery:
    start: int
    end: int
    query: str


def find_workspace_query(
    text: str,
    cursor_offset: int,
    accepted_ranges: tuple[tuple[int, int], ...] = (),
    slash_command: bool = False,
) -> WorkspaceQuery | None:
    if slash_command or not 0 <= cursor_offset <= len(text):
        return None
    line_start = text.rfind("\n", 0, cursor_offset) + 1
    at = text.rfind("@", line_start, cursor_offset + 1)
    if at < 0 or (at > 0 and not text[at - 1].isspace()):
        return None
    if any(start <= at < end for start, end in accepted_ranges):
        return None
    end = cursor_offset
    while end < len(text) and not text[end].isspace():
        end += 1
    if any(character.isspace() for character in text[at + 1:cursor_offset]):
        return None
    return WorkspaceQuery(at, end, text[at + 1:cursor_offset])


class WorkspaceCompletionController:
    def __init__(self, scorer: Callable = search_snapshot, *, debounce: float = 0.04) -> None:
        self._scorer = scorer
        self._debounce = debounce
        self._generation = 0
        self._task: asyncio.Task[tuple[int, WorkspaceQuery, tuple[WorkspacePathCandidate, ...]]] | None = None
        self.query: WorkspaceQuery | None = None
        self.candidates: tuple[WorkspacePathCandidate, ...] = ()

    def update(
        self,
        composer: ComposerInputSnapshot,
        query: WorkspaceQuery | None,
        index_snapshot: WorkspacePathSnapshot,
    ) -> asyncio.Task | None:
        self._generation += 1
        generation = self._generation
        self.query = query
        self.candidates = ()
        if self._task is not None:
            self._task.cancel()
        if query is None:
            self._task = None
            return None

        async def score():
            if query.query:
                await asyncio.sleep(self._debounce)
                result = await asyncio.to_thread(
                    self._scorer, index_snapshot, query.query, excluded={item.identity for item in composer.references}
                )
            else:
                result = self._scorer(index_snapshot, "", excluded={item.identity for item in composer.references})
            return generation, query, result

        self._task = asyncio.create_task(score())
        return self._task

    def accept_result(self, result: tuple[int, WorkspaceQuery, tuple[WorkspacePathCandidate, ...]]) -> bool:
        generation, query, candidates = result
        if generation != self._generation or query != self.query:
            return False
        self.candidates = candidates
        return True

    async def resolve_for_accept(self) -> WorkspacePathCandidate | None:
        task = self._task
        if task is None:
            return self.candidates[0] if self.candidates else None
        try:
            result = await task
        except asyncio.CancelledError:
            return None
        if not self.accept_result(result):
            return None
        return self.candidates[0] if self.candidates else None

    def close(self) -> None:
        self._generation += 1
        if self._task is not None:
            self._task.cancel()
        self._task = None
        self.query = None
        self.candidates = ()
