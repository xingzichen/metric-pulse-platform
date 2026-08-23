from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import get_settings
from .models import (
    AuditLog,
    CollectionTask,
    CollectionUnit,
    DataRecord,
    ReviewStatus,
    TaskEvent,
    TaskStatus,
    UnitStatus,
    User,
)
from .state_machine import ensure_transition
from .storage import FileObjectStore
from .workbook import read_rows


def add_event(db: Session, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
    db.add(TaskEvent(task_id=task_id, event_type=event_type, payload=payload))


def audit(
    db: Session,
    *,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: str,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before=before,
            after=after,
        )
    )


def business_key(sheet: str, row: dict[str, Any], fields: list[str], source_row: int) -> str:
    identity = [row.get(field) for field in fields]
    if not fields or all(value in (None, "") for value in identity):
        identity = [source_row]
    payload = json.dumps([sheet, identity], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def plan_task(db: Session, task: CollectionTask) -> dict[str, int]:
    from .models import FileRecord

    file = db.get(FileRecord, task.file_id)
    if not file or not file.analysis:
        raise ValueError("File is not analyzed")
    source = FileObjectStore().path(file.object_key)
    settings = get_settings()
    selections = task.config.get("datasets", [])
    analysis_by_name = {sheet["name"]: sheet for sheet in file.analysis["sheets"]}
    records_created = 0
    units_created = 0
    for selection in selections:
        sheet = analysis_by_name.get(selection["sheet_name"])
        if not sheet:
            raise ValueError(f"Unknown sheet {selection['sheet_name']!r}")
        headers = sheet["headers"]
        target_fields = [field for field in selection["target_fields"] if field in headers]
        descriptor_fields = [field for field in selection["descriptor_fields"] if field in headers]
        key_fields = [field for field in selection["business_key_fields"] if field in headers]
        mode = selection.get("mode", "row_contract_collect")
        row_source = source
        if mode == "snapshot_build" and settings.collector_mode == "gold":
            if not settings.gold_workbook_path:
                raise ValueError("Gold workbook path is required for snapshot acceptance mode")
            row_source = settings.gold_workbook_path
        for source_row, raw_data in read_rows(
            row_source,
            sheet_name=sheet["name"],
            header_row=sheet["header_row"],
            headers=headers,
        ):
            if mode == "snapshot_build":
                raw_data = {**raw_data, **dict.fromkeys(target_fields)}
                missing = target_fields
            else:
                missing = [field for field in target_fields if raw_data.get(field) in (None, "")]
            if not missing:
                continue
            contract = {
                "sheet_name": sheet["name"],
                "source_row": source_row,
                "descriptors": {field: raw_data.get(field) for field in descriptor_fields},
                "target_fields": target_fields,
                "mode": mode,
            }
            record = DataRecord(
                task_id=task.id,
                sheet_name=sheet["name"],
                source_row=source_row,
                business_key=business_key(sheet["name"], raw_data, key_fields, source_row),
                raw_data=raw_data,
                row_contract=contract,
            )
            db.add(record)
            db.flush()
            db.add(
                CollectionUnit(
                    task_id=task.id,
                    record_id=record.id,
                    run_version=task.run_version,
                    target_fields=missing,
                )
            )
            records_created += 1
            units_created += 1
    task.stats = {
        "total": units_created,
        "pending": units_created,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "reviewed": 0,
    }
    add_event(db, task.id, "task.planned", task.stats)
    db.commit()
    return {"records": records_created, "units": units_created}


def refresh_stats(db: Session, task: CollectionTask) -> dict[str, int]:
    unit_counts = dict(
        db.execute(
            select(CollectionUnit.status, func.count())
            .where(CollectionUnit.task_id == task.id)
            .group_by(CollectionUnit.status)
        ).all()
    )
    reviewed = (
        db.scalar(
            select(func.count())
            .select_from(CollectionUnit)
            .where(
                CollectionUnit.task_id == task.id,
                CollectionUnit.review_status != ReviewStatus.UNREVIEWED,
            )
        )
        or 0
    )
    total = sum(unit_counts.values())
    stats = {
        "total": total,
        "pending": unit_counts.get(UnitStatus.PENDING, 0) + unit_counts.get(UnitStatus.FAILED_RETRYABLE, 0),
        "running": unit_counts.get(UnitStatus.RUNNING, 0),
        "succeeded": unit_counts.get(UnitStatus.SUCCEEDED, 0),
        "failed": unit_counts.get(UnitStatus.FAILED_FINAL, 0),
        "discarded": unit_counts.get(UnitStatus.DISCARDED, 0),
        "reviewed": reviewed,
    }
    task.stats = stats
    return stats


def change_task_status(
    db: Session,
    task: CollectionTask,
    target: TaskStatus,
    *,
    actor: User | None = None,
) -> None:
    before = task.status
    ensure_transition(before, target)
    task.status = target
    task.version += 1
    task.updated_at = datetime.now(UTC)
    add_event(db, task.id, "task.status.changed", {"from": before, "to": target})
    audit(
        db,
        actor_id=actor.id if actor else None,
        action=f"task.{target.lower()}",
        resource_type="task",
        resource_id=task.id,
        before={"status": before},
        after={"status": target},
    )
    db.commit()


def start_task(db: Session, task: CollectionTask, actor: User | None = None) -> None:
    if task.status == TaskStatus.DRAFT:
        task.run_version += 1
        db.execute(
            CollectionUnit.__table__.update()
            .where(CollectionUnit.task_id == task.id)
            .values(run_version=task.run_version)
        )
    elif task.status in {TaskStatus.FAILED, TaskStatus.SUCCEEDED_WITH_ERRORS}:
        task.run_version += 1
        db.execute(
            CollectionUnit.__table__.update()
            .where(
                CollectionUnit.task_id == task.id,
                CollectionUnit.status.in_([UnitStatus.FAILED_FINAL, UnitStatus.FAILED_RETRYABLE]),
            )
            .values(status=UnitStatus.PENDING, run_version=task.run_version, error=None)
        )
        db.execute(
            CollectionUnit.__table__.update()
            .where(
                CollectionUnit.task_id == task.id,
                CollectionUnit.status == UnitStatus.PENDING,
            )
            .values(
                run_version=task.run_version,
                review_status=ReviewStatus.UNREVIEWED,
                final_values=None,
            )
        )
    change_task_status(db, task, TaskStatus.QUEUED, actor=actor)


def soft_delete_task(db: Session, task: CollectionTask, actor: User) -> None:
    change_task_status(db, task, TaskStatus.DELETED, actor=actor)
    task.deleted_at = datetime.now(UTC)
    db.commit()
