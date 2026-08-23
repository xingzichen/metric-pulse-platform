from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    CollectionTask,
    CollectionUnit,
    ExportJob,
    ExportStatus,
    FileRecord,
    ReviewDecision,
    ReviewStatus,
    TaskStatus,
    UnitStatus,
    User,
)
from .storage import FileObjectStore, export_path
from .task_service import add_event, audit, refresh_stats
from .workbook import export_reviewed_workbook


class ReviewConflict(ValueError):
    pass


def review_unit(
    db: Session,
    *,
    unit: CollectionUnit,
    actor: User,
    decision: ReviewStatus,
    expected_version: int,
    values: dict[str, Any] | None = None,
    comment: str | None = None,
) -> CollectionUnit:
    if unit.version != expected_version:
        raise ReviewConflict("Review version conflict")
    if unit.status != UnitStatus.SUCCEEDED:
        raise ValueError("Only succeeded units can be reviewed")
    before = unit.final_values
    if decision == ReviewStatus.APPROVED:
        final_values = unit.suggestion or {}
    elif decision == ReviewStatus.CORRECTED:
        if values is None or set(values) != set(unit.target_fields):
            raise ValueError("Corrected values must contain every target field")
        final_values = values
    elif decision == ReviewStatus.REJECTED:
        final_values = None
        unit.status = UnitStatus.PENDING
        unit.suggestion = None
        unit.error = None
    elif decision == ReviewStatus.SKIPPED:
        final_values = None
    else:
        raise ValueError(f"Unsupported decision: {decision}")

    unit.review_status = decision
    unit.final_values = final_values
    unit.version += 1
    db.add(
        ReviewDecision(
            unit_id=unit.id,
            actor_id=actor.id,
            decision=decision,
            before_values=before,
            after_values=final_values,
            comment=comment,
            unit_version=unit.version,
        )
    )
    task = db.get(CollectionTask, unit.task_id)
    if task:
        if decision == ReviewStatus.REJECTED and task.status == TaskStatus.SUCCEEDED:
            # A rejected row needs recollection, so make the task retryable.
            task.status = TaskStatus.SUCCEEDED_WITH_ERRORS
        task.version += 1
        db.query(ExportJob).filter(
            ExportJob.task_id == task.id,
            ExportJob.status == ExportStatus.READY,
        ).update({ExportJob.status: ExportStatus.STALE}, synchronize_session=False)
        refresh_stats(db, task)
        add_event(db, task.id, "review.stats.updated", task.stats)
    audit(
        db,
        actor_id=actor.id,
        action=f"review.{decision.lower()}",
        resource_type="collection_unit",
        resource_id=unit.id,
        before={"values": before},
        after={"values": final_values},
    )
    db.commit()
    return unit


def export_readiness(db: Session, task_id: str) -> dict[str, Any]:
    counts = dict(
        db.execute(
            select(CollectionUnit.review_status, func.count())
            .where(CollectionUnit.task_id == task_id)
            .group_by(CollectionUnit.review_status)
        ).all()
    )
    failed = (
        db.scalar(
            select(func.count())
            .select_from(CollectionUnit)
            .where(
                CollectionUnit.task_id == task_id,
                CollectionUnit.status.in_([UnitStatus.FAILED_FINAL, UnitStatus.FAILED_RETRYABLE]),
            )
        )
        or 0
    )
    unreviewed = counts.get(ReviewStatus.UNREVIEWED, 0)
    rejected = counts.get(ReviewStatus.REJECTED, 0)
    skipped = counts.get(ReviewStatus.SKIPPED, 0)
    blockers = []
    if unreviewed:
        blockers.append({"code": "UNREVIEWED", "count": unreviewed})
    if rejected:
        blockers.append({"code": "REJECTED", "count": rejected})
    if skipped:
        blockers.append({"code": "SKIPPED", "count": skipped})
    if failed:
        blockers.append({"code": "FAILED", "count": failed})
    return {
        "ready": not blockers,
        "blockers": blockers,
        "counts": {str(key): value for key, value in counts.items()},
    }


def build_export(db: Session, task: CollectionTask, actor: User) -> ExportJob:
    readiness = export_readiness(db, task.id)
    if not readiness["ready"]:
        raise ValueError("Task is not ready for export")
    file = db.get(FileRecord, task.file_id)
    if not file:
        raise ValueError("Source file not found")
    job = ExportJob(
        task_id=task.id,
        actor_id=actor.id,
        status=ExportStatus.BUILDING,
        task_version=task.version,
    )
    db.add(job)
    db.flush()
    try:
        updates = [
            (unit.record.sheet_name, unit.record.source_row, unit.final_values or {})
            for unit in db.scalars(
                select(CollectionUnit).where(
                    CollectionUnit.task_id == task.id,
                    CollectionUnit.review_status.in_([ReviewStatus.APPROVED, ReviewStatus.CORRECTED]),
                )
            )
        ]
        destination = export_path(f"{task.id}/{job.id}.xlsx")
        export_reviewed_workbook(FileObjectStore().path(file.object_key), destination, updates)
        key, digest = FileObjectStore().put_file(destination, namespace="exports", suffix=".xlsx")
        job.object_key = key
        job.content_hash = digest
        job.status = ExportStatus.READY
        job.completed_at = datetime.now(UTC)
        add_event(db, task.id, "export.status.changed", {"export_id": job.id, "status": "READY"})
    except Exception as exc:
        job.status = ExportStatus.FAILED
        job.error = str(exc)
        add_event(
            db,
            task.id,
            "export.status.changed",
            {"export_id": job.id, "status": "FAILED", "error": str(exc)},
        )
    audit(
        db,
        actor_id=actor.id,
        action="export.create",
        resource_type="export",
        resource_id=job.id,
        after={"status": job.status, "task_version": task.version},
    )
    db.commit()
    return job
