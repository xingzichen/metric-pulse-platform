"""任务状态机及前端可用操作推导。

PAUSING/STOPPING 是等待 worker 在安全边界确认的中间态，不能直接解释为已经暂停或停止；
所有控制操作都必须经过显式迁移表。
"""

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
    """验证单次迁移；调用方负责持久化状态和审计。"""

    source = TaskStatus(current)
    destination = TaskStatus(target)
    if destination not in ALLOWED_TRANSITIONS[source]:
        raise InvalidTransition(f"Cannot transition task from {source} to {destination}")


def allowed_actions(status: str | TaskStatus) -> list[str]:
    """从同一状态语义推导前端操作，避免 UI 自建不一致规则。"""

    value = TaskStatus(status)
    actions: list[str] = []
    if value == TaskStatus.DRAFT:
        actions.append("start")
    elif value == TaskStatus.PAUSED:
        actions.append("resume")
    elif value in {TaskStatus.FAILED, TaskStatus.SUCCEEDED_WITH_ERRORS}:
        actions.append("retry")
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
