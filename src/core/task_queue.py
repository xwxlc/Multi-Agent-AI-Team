"""Task queue with priority and dependency management."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"


class Priority(Enum):
    HIGH = 0
    MEDIUM = 1
    LOW = 2


@dataclass
class Task:
    id: str
    title: str
    description: str
    assigned_to: str  # agent role name
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    result: Any = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def is_ready(self, completed_ids: set[str], failed_ids: set[str]) -> bool:
        return all(dep in completed_ids or dep in failed_ids for dep in self.dependencies)


class TaskQueue:
    """Priority-based task queue with dependency resolution."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._completed: set[str] = set()
        self._failed: set[str] = set()

    def add(self, task: Task) -> None:
        self._tasks[task.id] = task

    def add_batch(self, tasks: list[Task]) -> None:
        for t in tasks:
            self.add(t)

    def get_next(self, role_filter: Optional[str] = None) -> Optional[Task]:
        """Get the highest-priority ready task, optionally filtered by role."""
        candidates = [
            t for t in self._tasks.values()
            if t.status == TaskStatus.PENDING
            and t.is_ready(self._completed, self._failed)
            and (role_filter is None or t.assigned_to == role_filter)
        ]
        candidates.sort(key=lambda t: t.priority.value)
        return candidates[0] if candidates else None

    def mark_in_progress(self, task_id: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = TaskStatus.IN_PROGRESS

    def mark_completed(self, task_id: str, result: Any = None) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = TaskStatus.COMPLETED
            self._tasks[task_id].result = result
            self._completed.add(task_id)

    def mark_failed(self, task_id: str, error: str) -> None:
        if task_id in self._tasks:
            self._tasks[task_id].status = TaskStatus.FAILED
            self._tasks[task_id].error = error
            self._failed.add(task_id)

    @property
    def all_done(self) -> bool:
        return all(
            t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for t in self._tasks.values()
        )

    def get_stats(self) -> dict:
        stats = {s: 0 for s in TaskStatus}
        for t in self._tasks.values():
            stats[t.status] += 1
        return {"by_status": {k.value: v for k, v in stats.items()}, "total": len(self._tasks)}

    def get_tasks_by_role(self, role: str) -> list[Task]:
        return [t for t in self._tasks.values() if t.assigned_to == role]

    def reset_for_retry(self, role: str) -> int:
        """Reset all tasks for a role back to PENDING for feedback loop."""
        count = 0
        for t in self._tasks.values():
            if t.assigned_to == role and t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                t.status = TaskStatus.PENDING
                t.error = None
                t.result = None
                count += 1
        # Clear completed/failed tracking for those tasks
        reset_ids = {t.id for t in self._tasks.values()
                     if t.assigned_to == role}
        self._completed -= reset_ids
        self._failed -= reset_ids
        return count
