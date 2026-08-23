from __future__ import annotations

from sqlalchemy import select

from metric_pulse.db import SessionLocal
from metric_pulse.models import (
    CollectionTask,
    CollectionUnit,
    DataRecord,
    FileRecord,
    FileStatus,
    ReviewStatus,
    TaskStatus,
    UnitStatus,
    User,
)
from metric_pulse.review_service import review_unit
from metric_pulse.task_service import start_task


def _task_with_unit(*, task_status: TaskStatus, unit_status: UnitStatus):
    db = SessionLocal()
    user = db.scalar(select(User).where(User.username == "admin"))
    file = FileRecord(
        owner_id=user.id,
        original_name="control.xlsx",
        content_hash="0" * 64,
        object_key="fixtures/control.xlsx",
        size=100,
        status=FileStatus.READY,
        analysis={"sheets": []},
    )
    db.add(file)
    db.flush()
    task = CollectionTask(
        owner_id=user.id,
        file_id=file.id,
        name="control-boundaries",
        status=task_status,
        run_version=1,
        stats={"total": 1, "pending": int(unit_status == UnitStatus.PENDING), "running": 0},
    )
    db.add(task)
    db.flush()
    record = DataRecord(
        task_id=task.id,
        sheet_name="Sheet1",
        source_row=2,
        business_key="1" * 64,
        raw_data={"name": "source"},
        row_contract={"descriptors": {"name": "source"}},
    )
    db.add(record)
    db.flush()
    unit = CollectionUnit(
        task_id=task.id,
        record_id=record.id,
        run_version=1,
        target_fields=["result"],
        status=unit_status,
        suggestion={"result": "model"} if unit_status == UnitStatus.SUCCEEDED else None,
    )
    db.add(unit)
    db.commit()
    return db, user, task, unit


def test_stopping_queued_task_converges_without_worker(client) -> None:
    db, _, task, unit = _task_with_unit(
        task_status=TaskStatus.QUEUED,
        unit_status=UnitStatus.PENDING,
    )
    task_id, version, unit_id = task.id, task.version, unit.id
    db.close()

    response = client.post(
        f"/api/v1/tasks/{task_id}/stop",
        json={"expected_version": version},
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "STOPPED"
    with SessionLocal() as check:
        assert check.get(CollectionUnit, unit_id).status == UnitStatus.DISCARDED


def test_rejected_review_becomes_retryable_and_clears_review(client) -> None:
    db, user, task, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.REJECTED,
        expected_version=unit.version,
    )
    assert task.status == TaskStatus.SUCCEEDED_WITH_ERRORS
    assert unit.status == UnitStatus.PENDING

    start_task(db, task, user)
    db.refresh(unit)
    assert task.status == TaskStatus.QUEUED
    assert unit.review_status == ReviewStatus.UNREVIEWED
    assert unit.run_version == task.run_version
    db.close()
