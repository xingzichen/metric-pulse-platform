from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from metric_pulse import main as main_module
from metric_pulse.collector import CollectionResult, EvidenceItem
from metric_pulse.dataset_profiles import (
    AI_ALGORITHM_COLLECTION_TARGET_FIELDS,
    FORBES_AI50_SOURCE_URL,
    GITHUB_TOP_REPOSITORIES_SOURCE_URL,
    TOP_LIST_AI_TARGET_FIELDS,
    ai_algorithm_collection_row_contract,
    top_list_ai_row_contract,
)
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


def test_ai_index_human_correction_recomputes_data_deterministically(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.target_fields = ["be_data", "be_unit", "data"]
    unit.record.row_contract = {
        "profile": "ai_index_v1",
        "standard_unit": "亿美元",
    }
    unit.suggestion = {"be_data": 500, "be_unit": "百万美元", "data": 5}
    unit.resolution_status = ResolutionStatus.RESOLVED
    db.commit()

    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.CORRECTED,
        expected_version=unit.version,
        values={"be_data": "600", "be_unit": "百万美元", "data": 999},
    )

    assert unit.final_values == {
        "be_data": "600",
        "be_unit": "百万美元",
        "data": 6,
    }
    assert unit.validation["conversion"]["mode"] == "DETERMINISTIC"
    assert unit.validation["conversion"]["factor"] == "0.01"
    assert unit.resolution_status == ResolutionStatus.RESOLVED
    db.close()


def test_algorithm_collection_correction_preserves_application_owned_fields(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    snapshot_at = "2026-08-24T21:30:00+08:00"
    raw_data, contract, target_fields = ai_algorithm_collection_row_contract(
        sheet_name="人工智能算法收藏(ai_algorithm_collectio",
        source_row=4,
        rank=1,
        snapshot_at=snapshot_at,
        headers=list(AI_ALGORITHM_COLLECTION_TARGET_FIELDS),
    )
    unit.target_fields = target_fields
    unit.record.raw_data = raw_data
    unit.record.row_contract = contract
    unit.suggestion = {
        **dict.fromkeys(target_fields),
        **contract["fixed_values"],
        "logic_id": "a" * 64,
        "name": "owner/original",
        "star": 999,
        "source_url": GITHUB_TOP_REPOSITORIES_SOURCE_URL,
    }
    unit.resolution_status = ResolutionStatus.RESOLVED
    db.commit()
    correction = {
        **unit.suggestion,
        "name": "owner/corrected",
        "star": "1001",
        "rank": 99,
        "source_url": "https://example.com/forged",
        "source_department": "Other",
        "collect_date": "2000-01-01",
    }

    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.CORRECTED,
        expected_version=unit.version,
        values=correction,
    )

    assert unit.final_values["name"] == "owner/corrected"
    assert unit.final_values["star"] == 1001
    assert unit.final_values["rank"] == 1
    assert unit.final_values["source_url"] == GITHUB_TOP_REPOSITORIES_SOURCE_URL
    assert unit.final_values["source_department"] == "Github"
    assert unit.final_values["collect_date"] == snapshot_at
    assert unit.final_values["datasource_date"] == snapshot_at
    assert unit.final_values["collection_date"] == snapshot_at
    assert len(unit.final_values["logic_id"]) == 64
    assert unit.validation["human_correction"] is True
    db.close()


def test_forbes_ai50_correction_preserves_official_batch_fields(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    snapshot_at = "2026-08-24T22:30:00+08:00"
    headers = [*TOP_LIST_AI_TARGET_FIELDS, "update_time", "created_time"]
    raw_data, contract, target_fields = top_list_ai_row_contract(
        sheet_name="TOP50企业排名(top_list_ai)",
        source_row=203,
        list_position=1,
        snapshot_at=snapshot_at,
        rank_year=2026,
        headers=headers,
        superseded_rows=list(range(153, 203)),
    )
    datasource_date = "2026-04-16T06:30:00-04:00"
    unit.target_fields = target_fields
    unit.record.raw_data = raw_data
    unit.record.row_contract = contract
    unit.suggestion = {
        **dict.fromkeys(target_fields),
        **contract["fixed_values"],
        "logic_id": "a" * 64,
        "company_name": "Original Company",
        "headquarter_location": "美国旧金山",
        "CEO": "Original CEO",
        "financing_amount": 3.92,
        "establish_date": 2020,
        "source_url": FORBES_AI50_SOURCE_URL,
        "datasource_date": datasource_date,
    }
    unit.validation = {
        "deterministic_profile_values": {"datasource_date": datasource_date}
    }
    unit.resolution_status = ResolutionStatus.RESOLVED
    db.commit()
    correction = {
        **unit.suggestion,
        "company_name": "Corrected Company",
        "headquarter_location": "美国加利福尼亚州旧金山",
        "CEO": "Corrected CEO",
        "financing_amount": "4.58",
        "establish_date": "2021",
        "rank_year": 1999,
        "financing_amount_unit": "美元",
        "source": "转载网站",
        "source_url": "https://example.com/forged",
        "datasource_date": "2000-01-01",
        "collection_date": "2000-01-01",
        "data_status": "删除",
    }

    review_unit(
        db,
        unit=unit,
        actor=user,
        decision=ReviewStatus.CORRECTED,
        expected_version=unit.version,
        values=correction,
    )

    assert unit.final_values["company_name"] == "Corrected Company"
    assert unit.final_values["headquarter_location"] == "美国加利福尼亚州旧金山"
    assert unit.final_values["CEO"] == "Corrected CEO"
    assert unit.final_values["financing_amount"] == 4.58
    assert unit.final_values["financing_amount_unit"] == "亿美元"
    assert unit.final_values["rank_year"] == 2026
    assert unit.final_values["source"] == "福布斯"
    assert unit.final_values["source_url"] == FORBES_AI50_SOURCE_URL
    assert unit.final_values["datasource_date"] == datasource_date
    assert unit.final_values["collection_date"] == snapshot_at
    assert unit.final_values["data_status"] == "新增"
    assert len(unit.final_values["logic_id"]) == 64
    assert unit.validation["human_correction"] is True
    db.close()


def test_ai_index_human_correction_rejects_dimension_mismatch(client) -> None:
    db, user, _, unit = _task_with_unit(
        task_status=TaskStatus.SUCCEEDED,
        unit_status=UnitStatus.SUCCEEDED,
    )
    unit.target_fields = ["be_data", "be_unit", "data"]
    unit.record.row_contract = {
        "profile": "ai_index_v1",
        "standard_unit": "EFlops",
    }
    unit.suggestion = {"be_data": 12, "be_unit": "亿美元", "data": None}
    unit.resolution_status = ResolutionStatus.INVALID
    db.commit()

    with pytest.raises(ValueError, match="DIMENSION_MISMATCH"):
        review_unit(
            db,
            unit=unit,
            actor=user,
            decision=ReviewStatus.CORRECTED,
            expected_version=unit.version,
            values={"be_data": 12, "be_unit": "亿美元", "data": 12},
        )
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


def test_permission_error_pauses_task_without_exhausting_unit_retries(client) -> None:
    class PermissionFailingCollector:
        async def collect(self, _record, _unit):
            raise PermissionError(13, "Permission denied", "/data/source-cache/locks")

    db, _, task, unit = _task_with_unit(
        task_status=TaskStatus.QUEUED,
        unit_status=UnitStatus.PENDING,
    )

    asyncio.run(TaskProcessor(PermissionFailingCollector()).process(db, task.id, max_units=1))

    db.refresh(task)
    db.refresh(unit)
    assert task.status == TaskStatus.PAUSED
    assert unit.status == UnitStatus.FAILED_RETRYABLE
    assert unit.attempt_count == 1
    assert unit.next_attempt_at is not None
    assert "/data/source-cache/locks" in unit.error
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
