"""由终端交互宿主支持的 TTY 权限提示器。"""

from __future__ import annotations

from services.permissions import PermissionRequest, PermissionResponse
from ui.cli.terminal.interaction_host import TerminalInteractionHost


class TtyPermissionPrompter:
    """轻量权限提示器；UI 所有权归属于交互宿主。"""

    def __init__(self, host: TerminalInteractionHost) -> None:
        self._host = host

    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        return await self._host.request_permission(request)


__all__ = ["TtyPermissionPrompter"]
