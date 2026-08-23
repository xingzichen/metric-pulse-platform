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
    from .processor import process_task_sync

    process_task_sync(task_id)


def dispatch_task(task_id: str) -> None:
    celery_app.send_task("metric_pulse.process_task", args=[task_id])
