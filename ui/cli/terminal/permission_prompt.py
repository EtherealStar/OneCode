"""TTY permission prompter backed by the terminal interaction host."""

from __future__ import annotations

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.terminal.interaction_host import TerminalInteractionHost


class TtyPermissionPrompter:
    """Thin permission prompter; UI ownership lives in the interaction host."""

    def __init__(self, host: TerminalInteractionHost) -> None:
        self._host = host

    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        return await self._host.request_permission(request)


__all__ = ["TtyPermissionPrompter"]
