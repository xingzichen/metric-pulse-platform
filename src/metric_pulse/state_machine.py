from __future__ import annotations

from .models import TaskStatus

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.DRAFT: {TaskStatus.QUEUED, TaskStatus.STOPPED, TaskStatus.DELETED},
    TaskStatus.QUEUED: {
        TaskStatus.RUNNING,
        TaskStatus.PAUSED,
        TaskStatus.STOPPING,
        TaskStatus.FAILED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.PAUSING,
        TaskStatus.STOPPING,
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED_WITH_ERRORS,
        TaskStatus.FAILED,
    },
    TaskStatus.PAUSING: {TaskStatus.PAUSED, TaskStatus.STOPPING, TaskStatus.FAILED},
    TaskStatus.PAUSED: {TaskStatus.QUEUED, TaskStatus.STOPPING, TaskStatus.DELETED},
    TaskStatus.STOPPING: {TaskStatus.STOPPED, TaskStatus.FAILED},
    TaskStatus.STOPPED: {TaskStatus.DELETED},
    TaskStatus.SUCCEEDED: {TaskStatus.DELETED},
    TaskStatus.SUCCEEDED_WITH_ERRORS: {TaskStatus.QUEUED, TaskStatus.DELETED},
    TaskStatus.FAILED: {TaskStatus.QUEUED, TaskStatus.DELETED},
    TaskStatus.DELETED: set(),
}


class InvalidTransition(ValueError):
    pass


def ensure_transition(current: str | TaskStatus, target: str | TaskStatus) -> None:
    source = TaskStatus(current)
    destination = TaskStatus(target)
    if destination not in ALLOWED_TRANSITIONS[source]:
        raise InvalidTransition(f"Cannot transition task from {source} to {destination}")


def allowed_actions(status: str | TaskStatus) -> list[str]:
    value = TaskStatus(status)
    actions: list[str] = []
    if value in {TaskStatus.DRAFT, TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.SUCCEEDED_WITH_ERRORS}:
        actions.append("start" if value == TaskStatus.DRAFT else "resume")
    if value == TaskStatus.RUNNING:
        actions.extend(["pause", "stop"])
    if value in {TaskStatus.QUEUED, TaskStatus.PAUSING, TaskStatus.PAUSED}:
        actions.append("stop")
    if value in {
        TaskStatus.DRAFT,
        TaskStatus.PAUSED,
        TaskStatus.STOPPED,
        TaskStatus.SUCCEEDED,
        TaskStatus.SUCCEEDED_WITH_ERRORS,
        TaskStatus.FAILED,
    }:
        actions.append("delete")
    return actions
