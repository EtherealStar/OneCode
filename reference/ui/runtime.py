from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """UI 可观察的模型配置；替换时一次性发布完整快照。"""

    model: str | None = None


class RuntimeConfigStore:
    def __init__(self, initial: RuntimeConfig | None = None) -> None:
        self._lock = Lock()
        self._value = initial or RuntimeConfig()

    def get(self) -> RuntimeConfig:
        with self._lock:
            return self._value

    def set_model(self, model: str) -> RuntimeConfig:
        if not model.strip():
            raise ValueError("模型名称不能为空")
        with self._lock:
            self._value = RuntimeConfig(model.strip())
            return self._value
