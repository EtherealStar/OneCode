"""Queued attachment source for background task notifications."""

from __future__ import annotations

from core.runtime_state import RuntimeState
from services.background_tasks.manager import BackgroundTaskManager


class BackgroundTaskNotificationSource:
    def __init__(self, manager: BackgroundTaskManager) -> None:
        self._manager = manager

    def collect(self, state: RuntimeState) -> tuple[dict[str, object], ...]:
        return self._manager.drain_notifications(state)
