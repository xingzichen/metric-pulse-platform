"""人工审核、来源补全与导出服务。

审核决策通过乐观版本号防止覆盖；驳回表示重新采集，不计入已核对。原本缺少
``source_url`` 的结果只有在唯一证据被选中时才能自动回填，多来源必须人工明确选择。
导出只使用最终值，并在结果或审核状态改变后使旧导出失效。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    CollectionTask,
    CollectionUnit,
    Evidence,
    ExportJob,
    ExportStatus,
    FileRecord,
    ResolutionStatus,
    ReviewDecision,
    ReviewStatus,
    TaskStatus,
    UnitStatus,
    User,
)
from .resolution import VALIDATION_VERSION, classify_resolution
from .review_policy import record_sample_outcome
from .storage import FileObjectStore, export_path
from .task_service import add_event, audit, refresh_stats
from .workbook import export_reviewed_workbook


class ReviewConflict(ValueError):
    """审核提交使用的单元版本已经过期。"""

    pass


def _complete_review_source_url(
    db: Session,
    unit: CollectionUnit,
    values: dict[str, Any],
) -> dict[str, Any]:
    """在人工确认结果时补全唯一的已选证据链接。

    有业务目标字段但仍全空时表示未解决，不应仅因存在搜索结果就填来源；只缺来源字段的
    单元则允许直接采用唯一已选证据。零个或多个链接都需要审核员明确处理。
    """

    if "source_url" not in unit.target_fields or values.get("source_url") not in (None, ""):
        return values
    value_fields = [field for field in unit.target_fields if field not in {"source", "source_url"}]
    if value_fields and not any(values.get(field) not in (None, "") for field in value_fields):
        return values
    selected_urls = {
        evidence.source_url
        for evidence in db.scalars(select(Evidence).where(Evidence.unit_id == unit.id)).all()
        if evidence.source_url and evidence.metadata_json.get("selected") is True
    }
    if len(selected_urls) == 1:
        return {**values, "source_url": selected_urls.pop()}
    raise ValueError(
        "Resolved values require one corresponding source URL; select or enter the supporting evidence link"
    )


def review_unit(
    db: Session,
    *,
    unit: CollectionUnit,
    actor: User,
    decision: ReviewStatus,
    expected_version: int,
    values: dict[str, Any] | None = None,
    comment: str | None = None,
    commit: bool = True,
) -> CollectionUnit:
    """应用一项人工决策并追加不可变 ReviewDecision。

    APPROVED 接受建议；CORRECTED 要求提交全部目标字段；REJECTED 把执行单元重新置为待采集；
    CONFIRMED_UNRESOLVED 必须附调查说明。``commit=False`` 仅供批量审核在外层统一提交。
    """

    if unit.version != expected_version:
        raise ReviewConflict("Review version conflict")
    if unit.status != UnitStatus.SUCCEEDED:
        raise ValueError("Only succeeded units can be reviewed")
    before = unit.final_values
    if decision == ReviewStatus.APPROVED:
        if unit.resolution_status != ResolutionStatus.RESOLVED:
            raise ValueError("Only resolved units can be approved")
        final_values = _complete_review_source_url(db, unit, unit.suggestion or {})
    elif decision == ReviewStatus.CORRECTED:
        if values is None or set(values) != set(unit.target_fields):
            raise ValueError("Corrected values must contain every target field")
        final_values = _complete_review_source_url(db, unit, values)
        status, reason, risk = classify_resolution(
            execution_status=unit.status,
            target_fields=list(unit.target_fields),
            values=final_values,
            validation={"valid": True, "evidence_approved": False},
        )
        unit.resolution_status = status
        unit.resolution_reason = "HUMAN_CORRECTION" if status == ResolutionStatus.RESOLVED else reason
        unit.risk_level = risk
        unit.validation_version = VALIDATION_VERSION
    elif decision == ReviewStatus.REJECTED:
        # 重采清除旧建议和错误，但历史证据与 ReviewDecision 仍保留用于审计。
        final_values = None
        unit.status = UnitStatus.PENDING
        unit.resolution_status = ResolutionStatus.NOT_EVALUATED
        unit.resolution_reason = "RECOLLECTION_REQUESTED"
        unit.suggestion = None
        unit.error = None
    elif decision == ReviewStatus.CONFIRMED_UNRESOLVED:
        if unit.resolution_status not in {
            ResolutionStatus.PARTIAL,
            ResolutionStatus.UNRESOLVED,
            ResolutionStatus.CONFLICT,
        }:
            raise ValueError("Only partial, unresolved, or conflicting units can be confirmed unresolved")
        if not comment or not comment.strip():
            raise ValueError("Confirmed unresolved requires an investigation comment")
        final_values = unit.suggestion or {}
    elif decision == ReviewStatus.SKIPPED:
        if unit.record.row_contract.get("optional") is not True:
            raise ValueError("Skipped is only allowed for explicitly optional units")
        final_values = None
    elif decision == ReviewStatus.AUTO_APPROVED:
        raise ValueError("Auto approval can only be produced by a published review policy")
    else:
        raise ValueError(f"Unsupported decision: {decision}")

    unit.review_status = decision
    unit.review_required = decision == ReviewStatus.REJECTED
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
            policy_id=unit.review_policy_id,
            metadata_json={"sampled": unit.review_sampled},
        )
    )
    record_sample_outcome(
        db,
        unit,
        incorrect=decision == ReviewStatus.REJECTED
        or (decision == ReviewStatus.CORRECTED and final_values != (unit.suggestion or {})),
    )
    task = db.get(CollectionTask, unit.task_id)
    if task:
        if decision == ReviewStatus.REJECTED and task.status == TaskStatus.SUCCEEDED:
            # 已结束任务出现重采行后转为“带错误完成”，从而在 UI 中重新提供恢复操作。
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
    if commit:
        db.commit()
    else:
        db.flush()
    return unit


def export_readiness(db: Session, task_id: str) -> dict[str, Any]:
    """逐单元计算导出阻塞原因，而不是仅检查任务执行状态。

    已解决数据必须通过审核；未解决/冲突数据必须被人工确认；只有显式 optional 单元可以
    SKIPPED。返回聚合原因便于前端直接告诉用户还需处理多少条。
    """

    units = db.scalars(select(CollectionUnit).where(CollectionUnit.task_id == task_id)).all()
    counts: dict[str, int] = {}
    blocker_counts: dict[str, int] = {}
    approved = {ReviewStatus.AUTO_APPROVED, ReviewStatus.APPROVED, ReviewStatus.CORRECTED}
    confirmable = {
        ResolutionStatus.PARTIAL,
        ResolutionStatus.UNRESOLVED,
        ResolutionStatus.CONFLICT,
    }
    for unit in units:
        counts[unit.review_status] = counts.get(unit.review_status, 0) + 1
        blocker: str | None = None
        if unit.status != UnitStatus.SUCCEEDED:
            blocker = "EXECUTION_INCOMPLETE"
        elif unit.resolution_status == ResolutionStatus.RESOLVED:
            if unit.review_status not in approved:
                blocker = "RESOLVED_NOT_APPROVED"
        elif unit.resolution_status in confirmable:
            if unit.review_status != ReviewStatus.CONFIRMED_UNRESOLVED:
                blocker = "UNRESOLVED_NOT_CONFIRMED"
        elif unit.resolution_status == ResolutionStatus.INVALID:
            blocker = "INVALID"
        else:
            blocker = "NOT_EVALUATED"
        if unit.review_status == ReviewStatus.SKIPPED and unit.record.row_contract.get("optional") is True:
            blocker = None
        if blocker:
            blocker_counts[blocker] = blocker_counts.get(blocker, 0) + 1
    blockers = [{"code": code, "count": count} for code, count in sorted(blocker_counts.items())]
    return {
        "ready": not blockers,
        "blockers": blockers,
        "counts": counts,
    }


def build_export(db: Session, task: CollectionTask, actor: User) -> ExportJob:
    """生成已审核 Excel 和独立的未解决清单，并记录导出版本。

    Excel 只写入自动通过、人工通过或人工修正的数据；确认未解决项不伪造空值写入，而是进入
    JSON 报告。任何异常都转换为 FAILED 导出任务，避免留下半成品 READY 记录。
    """

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
                    CollectionUnit.review_status.in_(
                        [ReviewStatus.AUTO_APPROVED, ReviewStatus.APPROVED, ReviewStatus.CORRECTED]
                    ),
                )
            )
        ]
        destination = export_path(f"{task.id}/{job.id}.xlsx")
        export_reviewed_workbook(FileObjectStore().path(file.object_key), destination, updates)
        key, digest = FileObjectStore().put_file(destination, namespace="exports", suffix=".xlsx")
        job.object_key = key
        job.content_hash = digest
        unresolved = [
            {
                "unit_id": unit.id,
                "sheet_name": unit.record.sheet_name,
                "source_row": unit.record.source_row,
                "business_key": unit.record.business_key,
                "resolution_status": unit.resolution_status,
                "resolution_reason": unit.resolution_reason,
                "values": unit.final_values,
                "validation": unit.validation,
            }
            for unit in db.scalars(
                select(CollectionUnit).where(
                    CollectionUnit.task_id == task.id,
                    CollectionUnit.review_status == ReviewStatus.CONFIRMED_UNRESOLVED,
                )
            )
        ]
        report = json.dumps(
            {"task_id": task.id, "generated_at": datetime.now(UTC).isoformat(), "items": unresolved},
            ensure_ascii=False,
            indent=2,
            default=str,
        ).encode()
        unresolved_key, _ = FileObjectStore().put_bytes(
            report, namespace="unresolved-reports", suffix=".json"
        )
        job.unresolved_object_key = unresolved_key
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
