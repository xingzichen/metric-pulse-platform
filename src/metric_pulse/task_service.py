"""任务规划、统计汇总、状态变更与审计。

规划把每个待补行转换为不可变 RowContract 和可重试 CollectionUnit；执行阶段只消费这些
单元，不重新推断字段。统计从数据库聚合，避免把旧 JSON 缓存当作真相。控制状态变化同时
写入事件流和审计日志。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    AuditLog,
    CollectionTask,
    CollectionUnit,
    DataRecord,
    ResolutionStatus,
    ReviewStatus,
    SourceAcquisitionAttempt,
    TaskEvent,
    TaskStatus,
    UnitStatus,
    User,
)
from .state_machine import ensure_transition
from .storage import FileObjectStore
from .workbook import read_rows

COMPLETED_REVIEW_STATUSES = {
    ReviewStatus.AUTO_APPROVED,
    ReviewStatus.APPROVED,
    ReviewStatus.CORRECTED,
    ReviewStatus.CONFIRMED_UNRESOLVED,
    ReviewStatus.SKIPPED,
}


def add_event(db: Session, task_id: str, event_type: str, payload: dict[str, Any]) -> None:
    """追加面向任务时间线/SSE 的领域事件；事务由调用方提交。"""

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
    """追加通用审计日志，保存操作者以及变更前后摘要。"""

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
    """用工作表和业务键生成稳定哈希；无业务键时退化为源行号。"""

    identity = [row.get(field) for field in fields]
    if not fields or all(value in (None, "") for value in identity):
        identity = [source_row]
    payload = json.dumps([sheet, identity], ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def plan_task(db: Session, task: CollectionTask) -> dict[str, int]:
    """读取已识别工作簿并为所有缺失目标生成记录和采集单元。

    ``row_contract_collect`` 只补当前空字段；``snapshot_build`` 强制把目标字段视为空，适合
    每次都需要重建的榜单快照。行契约只包含配置选中的描述字段，运行期不得另行推断。
    """

    from .models import FileRecord

    file = db.get(FileRecord, task.file_id)
    if not file or not file.analysis:
        raise ValueError("File is not analyzed")
    source = FileObjectStore().path(file.object_key)
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
        for source_row, raw_data in read_rows(
            source,
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


def refresh_stats(db: Session, task: CollectionTask) -> dict[str, Any]:
    """从明细表重新聚合执行、解决、审核和来源统计并更新任务快照。"""

    # SessionLocal 关闭 autoflush；聚合前必须 flush，否则刚修改的单元不会进入统计查询。
    db.flush()
    unit_counts = dict(
        db.execute(
            select(CollectionUnit.status, func.count())
            .where(CollectionUnit.task_id == task.id)
            .group_by(CollectionUnit.status)
        ).all()
    )
    resolution_counts = dict(
        db.execute(
            select(CollectionUnit.resolution_status, func.count())
            .where(CollectionUnit.task_id == task.id)
            .group_by(CollectionUnit.resolution_status)
        ).all()
    )
    review_counts = dict(
        db.execute(
            select(CollectionUnit.review_status, func.count())
            .where(CollectionUnit.task_id == task.id)
            .group_by(CollectionUnit.review_status)
        ).all()
    )
    acquisition_counts = dict(
        db.execute(
            select(SourceAcquisitionAttempt.route, func.count())
            .join(CollectionUnit, CollectionUnit.id == SourceAcquisitionAttempt.unit_id)
            .where(CollectionUnit.task_id == task.id)
            .group_by(SourceAcquisitionAttempt.route)
        ).all()
    )
    cache_hits = (
        db.scalar(
            select(func.count())
            .select_from(SourceAcquisitionAttempt)
            .join(CollectionUnit, CollectionUnit.id == SourceAcquisitionAttempt.unit_id)
            .where(
                CollectionUnit.task_id == task.id,
                SourceAcquisitionAttempt.cache_hit.is_(True),
            )
        )
        or 0
    )
    total = sum(unit_counts.values())
    reviewed = sum(review_counts.get(status, 0) for status in COMPLETED_REVIEW_STATUSES)
    stats = {
        "total": total,
        "pending": unit_counts.get(UnitStatus.PENDING, 0) + unit_counts.get(UnitStatus.FAILED_RETRYABLE, 0),
        "running": unit_counts.get(UnitStatus.LEASED, 0) + unit_counts.get(UnitStatus.RUNNING, 0),
        "succeeded": unit_counts.get(UnitStatus.SUCCEEDED, 0),
        "failed": unit_counts.get(UnitStatus.FAILED_FINAL, 0),
        "discarded": unit_counts.get(UnitStatus.DISCARDED, 0),
        "reviewed": reviewed,
        "resolved": resolution_counts.get(ResolutionStatus.RESOLVED, 0),
        "executionCounts": {str(key): value for key, value in unit_counts.items()},
        "resolutionCounts": {str(key): value for key, value in resolution_counts.items()},
        "reviewCounts": {str(key): value for key, value in review_counts.items()},
        "acquisitionCounts": {str(key): value for key, value in acquisition_counts.items()},
        "sourceCacheHits": cache_hits,
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
    """校验状态机、更新乐观版本并同时写入事件与审计。"""

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
    """开始或重试任务，并为新运行版本重置应重采单元的生命周期字段。

    从 PAUSED 恢复不增加运行版本，因此可继续剩余单元；从失败状态整体重试会增加版本，保证
    旧 worker 即使稍后返回也不能写入新一轮结果。
    """

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
                resolution_status=ResolutionStatus.NOT_EVALUATED,
                resolution_reason=None,
                review_status=ReviewStatus.UNREVIEWED,
                review_required=True,
                final_values=None,
                lease_owner=None,
                leased_until=None,
                next_attempt_at=None,
            )
        )
    change_task_status(db, task, TaskStatus.QUEUED, actor=actor)


def soft_delete_task(db: Session, task: CollectionTask, actor: User) -> None:
    """通过状态机执行软删除，保留审计和历史关联数据。"""

    change_task_status(db, task, TaskStatus.DELETED, actor=actor)
    task.deleted_at = datetime.now(UTC)
    db.commit()
