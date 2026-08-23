from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .collector import Collector, configured_collector
from .models import (
    CollectionAttempt,
    CollectionTask,
    CollectionUnit,
    Evidence,
    TaskStatus,
    UnitStatus,
)
from .task_service import add_event, refresh_stats


class TaskProcessor:
    def __init__(self, collector: Collector | None = None) -> None:
        self.collector = collector or configured_collector()

    async def process(self, db: Session, task_id: str, *, max_units: int | None = None) -> None:
        task = db.get(CollectionTask, task_id)
        if not task or task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            return
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.RUNNING
            task.version += 1
            add_event(db, task.id, "task.status.changed", {"from": "QUEUED", "to": "RUNNING"})
            db.commit()
        processed = 0
        while max_units is None or processed < max_units:
            db.refresh(task)
            if task.status in {TaskStatus.PAUSING, TaskStatus.PAUSED}:
                if task.status == TaskStatus.PAUSING:
                    task.status = TaskStatus.PAUSED
                    task.version += 1
                    add_event(db, task.id, "task.status.changed", {"from": "PAUSING", "to": "PAUSED"})
                    db.commit()
                return
            if task.status in {TaskStatus.STOPPING, TaskStatus.STOPPED, TaskStatus.DELETED}:
                if task.status == TaskStatus.STOPPING:
                    task.status = TaskStatus.STOPPED
                    task.version += 1
                    db.query(CollectionUnit).filter(
                        CollectionUnit.task_id == task.id,
                        CollectionUnit.status.in_([UnitStatus.PENDING, UnitStatus.FAILED_RETRYABLE]),
                    ).update({CollectionUnit.status: UnitStatus.DISCARDED}, synchronize_session=False)
                    add_event(db, task.id, "task.status.changed", {"from": "STOPPING", "to": "STOPPED"})
                    refresh_stats(db, task)
                    db.commit()
                return
            unit = db.scalar(
                select(CollectionUnit)
                .where(
                    CollectionUnit.task_id == task.id,
                    CollectionUnit.run_version == task.run_version,
                    CollectionUnit.status.in_([UnitStatus.PENDING, UnitStatus.FAILED_RETRYABLE]),
                )
                .order_by(CollectionUnit.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if not unit:
                break
            unit.status = UnitStatus.RUNNING
            unit.started_at = unit.started_at or datetime.now(UTC)
            unit.attempt_count += 1
            attempt = CollectionAttempt(
                unit_id=unit.id,
                step="COLLECT",
                status="RUNNING",
                input_summary={"target_fields": unit.target_fields},
            )
            db.add(attempt)
            db.commit()
            try:
                result = await self.collector.collect(unit.record, unit)
                db.refresh(task)
                if task.run_version != unit.run_version or task.status in {
                    TaskStatus.STOPPING,
                    TaskStatus.STOPPED,
                    TaskStatus.DELETED,
                }:
                    unit.status = UnitStatus.DISCARDED
                else:
                    unit.suggestion = result.values
                    unit.validation = result.validation
                    unit.status = UnitStatus.SUCCEEDED
                    unit.finished_at = datetime.now(UTC)
                    for item in result.evidence:
                        db.add(
                            Evidence(
                                unit_id=unit.id,
                                source_url=item.source_url,
                                source_title=item.title,
                                locator=item.locator,
                                excerpt=item.excerpt,
                                metadata_json=item.metadata,
                            )
                        )
                attempt.status = "SUCCEEDED"
                attempt.model = result.model
                attempt.output_summary = {"fields": list(result.values), "validation": result.validation}
                attempt.ended_at = datetime.now(UTC)
            except Exception as exc:  # worker boundary records and contains provider failures
                unit.error = str(exc)
                unit.status = (
                    UnitStatus.FAILED_RETRYABLE if unit.attempt_count < 3 else UnitStatus.FAILED_FINAL
                )
                attempt.status = "FAILED"
                attempt.error = str(exc)
                attempt.ended_at = datetime.now(UTC)
            processed += 1
            refresh_stats(db, task)
            add_event(db, task.id, "task.stats.updated", task.stats)
            db.commit()
            await asyncio.sleep(0)

        db.refresh(task)
        stats = refresh_stats(db, task)
        if stats["pending"] == 0 and stats["running"] == 0:
            task.status = TaskStatus.SUCCEEDED_WITH_ERRORS if stats["failed"] else TaskStatus.SUCCEEDED
            task.version += 1
            add_event(
                db,
                task.id,
                "task.status.changed",
                {"to": task.status, "stats": stats},
            )
        db.commit()


def process_task_sync(task_id: str, collector: Collector | None = None) -> None:
    from .db import SessionLocal

    with SessionLocal() as db:
        asyncio.run(TaskProcessor(collector).process(db, task_id))
