"""供交互式用户界面使用的权限提示器协议。"""

from __future__ import annotations

from typing import Protocol

from services.permissions.types import PermissionRequest, PermissionResponse


class PermissionPrompter(Protocol):
    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        ...
