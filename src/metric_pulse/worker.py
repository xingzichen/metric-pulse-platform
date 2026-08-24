"""Celery worker 命令行入口；并发固定为 1 以匹配本地模型能力。"""

from __future__ import annotations

from .celery_app import celery_app


def run() -> None:
    """启动单并发 worker，防止多个任务同时争用唯一 OMLX 模型。"""

    celery_app.worker_main(["worker", "--loglevel=INFO", "--concurrency=1"])
