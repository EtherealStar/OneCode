"""In-process background task lifecycle services."""

from services.background_tasks.ids import generate_background_task_id
from services.background_tasks.manager import BackgroundTaskManager
from services.background_tasks.notifications import BackgroundTaskNotificationSource
from services.background_tasks.output import (
    background_task_output_dir,
    background_task_output_path,
)
from services.background_tasks.types import (
    BackgroundTaskState,
    BackgroundTaskStatus,
    BackgroundTaskType,
)

__all__ = [
    "BackgroundTaskManager",
    "BackgroundTaskNotificationSource",
    "BackgroundTaskState",
    "BackgroundTaskStatus",
    "BackgroundTaskType",
    "background_task_output_dir",
    "background_task_output_path",
    "generate_background_task_id",
]
