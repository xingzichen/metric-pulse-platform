"""Celery 异步任务入口。

开发环境通常使用 FastAPI ``BackgroundTasks`` 直接执行；部署环境关闭 eager 模式后，
API 只负责投递任务，由本模块注册的 Celery worker 执行同一个处理器。这样两种运行方式
共享状态机和持久化逻辑，不产生两套业务语义。
"""

from __future__ import annotations

from celery import Celery

from .config import get_settings

settings = get_settings()
celery_app = Celery(
    "metric_pulse",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)


@celery_app.task(name="metric_pulse.process_task", autoretry_for=(Exception,), retry_backoff=True)
def process_task(task_id: str) -> None:
    """Celery 可重试包装器；实际幂等与租约恢复均由处理器负责。"""

    from .processor import process_task_sync

    process_task_sync(task_id)


def dispatch_task(task_id: str) -> None:
    """只投递任务标识，避免把易过期的任务快照放进消息队列。"""

    celery_app.send_task("metric_pulse.process_task", args=[task_id])
