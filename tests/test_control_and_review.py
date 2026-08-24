from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from metric_pulse import main as main_module
from metric_pulse.collector import CollectionResult, EvidenceItem
from metric_pulse.db import SessionLocal
from metric_pulse.models import (
    CollectionTask,
    CollectionUnit,
    DataRecord,
    Evidence,
    FileRecord,
    FileStatus,
    ResolutionStatus,
    ReviewPolicy,
    ReviewStatus,
    RiskLevel,
    TaskStatus,
    UnitSourceLink,
    UnitStatus,
    User,
)
from metric_pulse.processor import TaskProcessor, persist_collection_audit
from metric_pulse.review_policy import apply_published_policy
from metric_pulse.review_service import export_readiness, review_unit
from metric_pulse.task_service import refresh_stats, start_task


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


def test_collection_audit_deduplicates_same_snapshot_within_one_result(client) -> None:
    db, _, _, unit = _task_with_unit(
        task_status=TaskStatus.RUNNING,
        unit_status=UnitStatus.RUNNING,
    )
    duplicate = EvidenceItem(
        source_url="https://example.com/data.csv",
        title="Official data",
        excerpt="region,data\nEC,42",
        metadata={"selected": True},
    )

    persist_collection_audit(
        db,
        unit,
        CollectionResult(values={"result": 42}, evidence=[duplicate, duplicate]),
    )
    db.commit()

    links = db.scalars(select(UnitSourceLink).where(UnitSourceLink.unit_id == unit.id)).all()
    assert len(links) == 1
    db.close()


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
    assert task.stats["reviewed"] == 0
    assert task.stats["reviewCounts"][ReviewStatus.REJECTED] == 1

    start_task(db, task, user)
    db.refresh(unit)
    assert task.status == TaskStatus.QUEUED
    assert unit.review_status == ReviewStatus.UNREVIEWED
    assert unit.run_version == task.run_version
    db.close()


def test_source_repair_preview_is_read_only_and_flags_legacy_source_bypass(client) -> None:
    db, _, task, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.record.raw_data = {
        "source_url": "https://example.com/official.csv",
        "region": "EC",
    }
    db.add(
        Evidence(
            unit_id=unit.id,
            source_url="https://example.net/search-result",
            source_title="Search result",
            metadata_json={"selected": True},
        )
    )
    db.commit()
    task_id = task.id
    db.close()

    response = client.get(f"/api/v1/tasks/{task_id}/source-repair-preview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["readOnly"] is True
    assert payload["applyRequiresConfirmation"] is True
    assert payload["total"] == 1
    assert payload["items"][0]["reason"] == "LEGACY_ROUTE_NOT_AUDITED"
    assert payload["items"][0]["canApply"] is False


def test_human_correction_autofills_one_selected_evidence_url(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.target_fields = ["result", "source_url"]
    unit.suggestion = {"result": None, "source_url": None}
    unit.resolution_status = ResolutionStatus.UNRESOLVED
    db.add(
        Evidence(
            unit_id=unit.id,
            source_url="https://example.com/supporting-attachment.pdf",
            source_title="Supporting attachment",
            metadata_json={"selected": True},
        )
    )
    db.commit()

    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.CORRECTED,
        expected_version=unit.version,
        values={"result": "corrected", "source_url": None},
    )

    assert unit.final_values == {
        "result": "corrected",
        "source_url": "https://example.com/supporting-attachment.pdf",
    }
    assert unit.resolution_status == ResolutionStatus.RESOLVED
    db.close()


def test_provenance_only_human_correction_autofills_selected_evidence_url(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.target_fields = ["source", "source_url"]
    unit.suggestion = {"source": None, "source_url": None}
    unit.resolution_status = ResolutionStatus.UNRESOLVED
    db.add(
        Evidence(
            unit_id=unit.id,
            source_url="https://example.com/official",
            source_title="Official",
            metadata_json={"selected": True},
        )
    )
    db.commit()

    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.CORRECTED,
        expected_version=unit.version,
        values={"source": "Official", "source_url": None},
    )

    assert unit.final_values == {
        "source": "Official",
        "source_url": "https://example.com/official",
    }
    assert unit.resolution_status == ResolutionStatus.RESOLVED
    db.close()


def test_human_correction_requires_source_choice_when_selected_evidence_is_ambiguous(
    client,
) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.target_fields = ["result", "source_url"]
    unit.suggestion = {"result": None, "source_url": None}
    unit.resolution_status = ResolutionStatus.UNRESOLVED
    for index in (1, 2):
        db.add(
            Evidence(
                unit_id=unit.id,
                source_url=f"https://example.com/source-{index}",
                source_title=f"Source {index}",
                metadata_json={"selected": True},
            )
        )
    db.commit()

    with pytest.raises(ValueError, match="corresponding source URL"):
        review_unit(
            db,
            unit=unit,
            actor=user,
            decision=ReviewStatus.CORRECTED,
            expected_version=unit.version,
            values={"result": "corrected", "source_url": None},
        )
    db.close()


def test_running_task_recollection_starts_a_new_review_lifecycle(client) -> None:
    class RecollectionCollector:
        async def collect(self, _record, unit):
            return CollectionResult(
                values={field: "recollected" for field in unit.target_fields},
                validation={"valid": True, "evidence_approved": False},
                model="test-model",
            )

    db, user, task, unit = _task_with_unit(
        task_status=TaskStatus.RUNNING,
        unit_status=UnitStatus.SUCCEEDED,
    )
    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.REJECTED,
        expected_version=unit.version,
    )
    assert unit.status == UnitStatus.PENDING
    assert unit.review_status == ReviewStatus.REJECTED
    assert task.stats["reviewed"] == 0

    asyncio.run(TaskProcessor(RecollectionCollector()).process(db, task.id, max_units=1))

    db.refresh(unit)
    db.refresh(task)
    assert unit.status == UnitStatus.SUCCEEDED
    assert unit.review_status == ReviewStatus.UNREVIEWED
    assert unit.review_required is True
    assert unit.final_values is None
    assert task.stats["reviewed"] == 0
    assert task.stats["reviewCounts"][ReviewStatus.UNREVIEWED] == 1
    db.close()


def test_start_refuses_unavailable_omlx_without_mutating_task(client, monkeypatch) -> None:
    db, _, task, _ = _task_with_unit(
        task_status=TaskStatus.DRAFT,
        unit_status=UnitStatus.PENDING,
    )
    task_id, version = task.id, task.version
    db.close()

    async def unavailable(_self):
        raise RuntimeError("provider rejected credentials")

    monkeypatch.setattr(main_module.OMLXClient, "health", unavailable)

    response = client.post(
        f"/api/v1/tasks/{task_id}/start",
        json={"expected_version": version},
    )

    assert response.status_code == 503
    assert "Collection model is unavailable" in response.json()["detail"]
    with SessionLocal() as check:
        persisted = check.get(CollectionTask, task_id)
        assert persisted.status == TaskStatus.DRAFT
        assert persisted.version == version


def test_successful_retry_clears_previous_error(client) -> None:
    class SuccessfulCollector:
        async def collect(self, _record, unit):
            return CollectionResult(
                values={field: None for field in unit.target_fields},
                validation={"valid": False, "missing_fields": unit.target_fields},
                model="test-model",
            )

    db, _, task, unit = _task_with_unit(
        task_status=TaskStatus.QUEUED,
        unit_status=UnitStatus.FAILED_RETRYABLE,
    )
    unit.error = "stale provider error"
    unit.attempt_count = 2
    db.commit()

    asyncio.run(TaskProcessor(SuccessfulCollector()).process(db, task.id, max_units=1))

    db.refresh(unit)
    assert unit.status == UnitStatus.SUCCEEDED
    assert unit.error is None
    assert unit.attempt_count == 3
    db.close()


def test_refresh_stats_includes_unflushed_unit_transition(client) -> None:
    db, _, task, unit = _task_with_unit(
        task_status=TaskStatus.RUNNING,
        unit_status=UnitStatus.RUNNING,
    )
    unit.status = UnitStatus.SUCCEEDED

    stats = refresh_stats(db, task)

    assert stats["running"] == 0
    assert stats["succeeded"] == 1
    db.rollback()
    db.close()


def test_processor_persists_running_stats_before_collection(client) -> None:
    observed: dict[str, int] = {}

    class InspectingCollector:
        async def collect(self, _record, unit):
            with SessionLocal() as check:
                persisted = check.get(CollectionTask, unit.task_id)
                observed.update(persisted.stats)
            return CollectionResult(
                values={field: None for field in unit.target_fields},
                validation={"valid": False, "missing_fields": unit.target_fields},
                model="test-model",
            )

    db, _, task, _ = _task_with_unit(
        task_status=TaskStatus.QUEUED,
        unit_status=UnitStatus.PENDING,
    )

    asyncio.run(TaskProcessor(InspectingCollector()).process(db, task.id, max_units=1))

    assert observed["pending"] == 0
    assert observed["running"] == 1
    db.close()


def test_confirmed_unresolved_requires_investigation_and_unblocks_export(client) -> None:
    db, user, task, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.resolution_status = ResolutionStatus.UNRESOLVED
    unit.resolution_reason = "NO_SUPPORTED_VALUE"
    unit.suggestion = {"result": None}
    db.commit()

    with pytest.raises(ValueError, match="investigation comment"):
        review_unit(
            db,
            unit=unit,
            actor=user,
            decision=ReviewStatus.CONFIRMED_UNRESOLVED,
            expected_version=unit.version,
        )
    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.CONFIRMED_UNRESOLVED,
        expected_version=unit.version,
        comment="Searched authoritative sources; no current value is published.",
    )

    assert export_readiness(db, task.id)["ready"] is True
    db.close()


def test_bulk_review_rejects_changed_snapshot(client) -> None:
    db, _, task, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.resolution_status = ResolutionStatus.RESOLVED
    unit.risk_level = RiskLevel.LOW
    db.commit()
    task_id, unit_id = task.id, unit.id
    db.close()

    preview = client.post(
        f"/api/v1/tasks/{task_id}/reviews/bulk/preview",
        json={"unit_ids": [unit_id]},
    )
    assert preview.status_code == 200, preview.text
    with SessionLocal() as change:
        changed = change.get(CollectionUnit, unit_id)
        changed.version += 1
        change.commit()
    commit = client.post(
        f"/api/v1/tasks/{task_id}/reviews/bulk/commit",
        json={"preview_token": preview.json()["previewToken"]},
    )
    assert commit.status_code == 409


def test_published_policy_can_auto_approve_low_risk_resolved_unit(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.RUNNING,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.resolution_status = ResolutionStatus.RESOLVED
    unit.risk_level = RiskLevel.LOW
    unit.validation = {"valid": True, "evidence_approved": True, "conflicts": []}
    policy = ReviewPolicy(
        name="low-risk",
        version=1,
        status="PUBLISHED",
        rules={"max_risk": "LOW"},
        sample_rate=0,
        max_sample_error_rate=0.02,
        created_by=user.id,
        published_at=datetime.now(UTC),
    )
    db.add(policy)
    db.commit()

    assert apply_published_policy(db, unit) is True
    assert unit.review_status == ReviewStatus.AUTO_APPROVED
    assert unit.review_required is False
    db.commit()
    db.close()
