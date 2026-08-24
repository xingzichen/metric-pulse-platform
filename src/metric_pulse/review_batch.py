"""批量审核的预览—提交两阶段协议。

预览冻结单元 ID 与版本号并返回短期令牌；提交时再次检查版本，期间任一单元发生变化都会
拒绝整批操作，避免覆盖已经重采或被其他审核员修改的结果。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    CollectionUnit,
    ResolutionStatus,
    ReviewBatch,
    ReviewStatus,
    UnitStatus,
    User,
)
from .review_service import ReviewConflict, review_unit


def create_review_preview(
    db: Session,
    *,
    task_id: str,
    actor: User,
    decision: str,
    unit_ids: list[str],
    risk_levels: list[str],
    comment: str | None,
) -> ReviewBatch:
    """筛选当前仍可批准的已解决单元，并冻结其版本号到短期批次。"""

    if decision != ReviewStatus.APPROVED:
        raise ValueError("Bulk review currently supports APPROVED only")
    query = select(CollectionUnit).where(
        CollectionUnit.task_id == task_id,
        CollectionUnit.status == UnitStatus.SUCCEEDED,
        CollectionUnit.resolution_status == ResolutionStatus.RESOLVED,
        CollectionUnit.review_status == ReviewStatus.UNREVIEWED,
    )
    if unit_ids:
        query = query.where(CollectionUnit.id.in_(unit_ids))
    if risk_levels:
        query = query.where(CollectionUnit.risk_level.in_(risk_levels))
    units = db.scalars(query.order_by(CollectionUnit.id)).all()
    versions = {unit.id: unit.version for unit in units}
    requested = len(set(unit_ids)) if unit_ids else len(units)
    preview: dict[str, Any] = {
        "eligible": len(units),
        "excluded": max(0, requested - len(units)),
        "sample": [
            {
                "unit_id": unit.id,
                "sheet_name": unit.record.sheet_name,
                "source_row": unit.record.source_row,
                "risk_level": unit.risk_level,
                "resolution_status": unit.resolution_status,
            }
            for unit in units[:10]
        ],
    }
    batch = ReviewBatch(
        token=secrets.token_urlsafe(32),
        task_id=task_id,
        actor_id=actor.id,
        decision=decision,
        comment=comment,
        filters={"unit_ids": unit_ids, "risk_levels": risk_levels},
        unit_versions=versions,
        preview=preview,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )
    db.add(batch)
    db.commit()
    return batch


def commit_review_preview(
    db: Session,
    *,
    token: str,
    task_id: str,
    actor: User,
) -> int:
    """验证批次未使用、未过期且所有单元版本不变，再在同一事务提交。"""

    batch = db.get(ReviewBatch, token)
    if batch is None or batch.task_id != task_id or batch.actor_id != actor.id:
        raise ValueError("Review preview token not found")
    expires_at = batch.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if batch.status != "PREVIEWED" or expires_at <= datetime.now(UTC):
        raise ValueError("Review preview token is expired or already used")
    units = db.scalars(
        select(CollectionUnit).where(CollectionUnit.id.in_(list(batch.unit_versions)))
    ).all()
    by_id = {unit.id: unit for unit in units}
    for unit_id, version in batch.unit_versions.items():
        unit = by_id.get(unit_id)
        if (
            unit is None
            or unit.version != version
            or unit.status != UnitStatus.SUCCEEDED
            or unit.resolution_status != ResolutionStatus.RESOLVED
            or unit.review_status != ReviewStatus.UNREVIEWED
        ):
            batch.status = "CONFLICT"
            db.commit()
            raise ReviewConflict("Bulk review snapshot changed; create a new preview")
    for unit in units:
        review_unit(
            db,
            unit=unit,
            actor=actor,
            decision=ReviewStatus.APPROVED,
            expected_version=unit.version,
            comment=batch.comment,
            commit=False,
        )
    batch.status = "COMMITTED"
    batch.committed_at = datetime.now(UTC)
    db.commit()
    return len(units)
