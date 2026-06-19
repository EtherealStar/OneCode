"""Adapter from :func:`ui.cli.suggestions.suggestions_for` to prompt_toolkit.

prompt_toolkit's :class:`prompt_toolkit.completion.Completer` is the
right abstraction for the inline model: it lets the framework own the
completion menu rendering and the up/down navigation through its
native :attr:`Buffer.complete_state`, while we only translate the
existing :class:`ui.cli.suggestions.SuggestionItem` objects into
:class:`prompt_toolkit.completion.Completion` objects.

The Enter/Tab semantics (execplan §M3) are implemented in
:mod:`ui.cli.terminal.prompt_session` by reading
``buffer.complete_state`` directly — this module only has to compute
the right ``start_position`` so an accepted completion replaces the
correct slice of the input.
"""

from __future__ import annotations

from typing import Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from ui.cli.suggestions import SuggestionItem, suggestions_for
from ui.cli.types import CliRuntime


class InlineCompleter(Completer):
    """prompt_toolkit Completer backed by :func:`suggestions_for`."""

    def __init__(self, runtime: CliRuntime | None) -> None:
        self._runtime = runtime
        # Exposed for tests and the live status line; mirrors the most
        # recent ``suggestions_for`` result.
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
        """Read-only view of the last computed suggestions (tests)."""

        return self._last_items


def _start_position(text: str, cursor: int, item: SuggestionItem) -> int:
    """How many characters before the cursor an accepted completion
    should replace.

    :func:`suggestions_for` returns the *full* candidate string for
    both slash commands and ``@file`` mentions, so the replacement
    must delete the partial token under the cursor first.
    """

    before = text[:cursor]
    if item.kind in {"command", "session"}:
        # Replace the whole command/session token typed so far.
        if before.startswith("/"):
            return -cursor
        return 0
    if item.kind in {"file", "directory"}:
        at_index = before.rfind("@")
        if at_index < 0:
            return 0
        # Replace from just after the '@' to the cursor. The
        # replacement value does not include the '@', so we keep the
        # '@' in place and replace only the path fragment.
        return (at_index + 1) - cursor
    return 0
