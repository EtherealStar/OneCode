"""Permission prompter protocol used by interactive UIs."""

from __future__ import annotations

from typing import Protocol

from services.permissions.types import PermissionRequest, PermissionResponse


class PermissionPrompter(Protocol):
    async def request_permission(
        self,
        request: PermissionRequest,
    ) -> PermissionResponse:
        ...
