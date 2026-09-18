"""运行时生命周期钩子服务。"""

from services.hooks.events import HookEvent
from services.hooks.registry import HookPayload, HookRegistry, HookResult

__all__ = ["HookEvent", "HookPayload", "HookRegistry", "HookResult"]
