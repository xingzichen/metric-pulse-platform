"""低风险自动审核策略与抽样质量控制。

只有已发布策略会生效，且单元必须已解决、证据通过、风险不高于阈值。稳定哈希保证同一单元
重复计算时抽样结果一致；抽样错误率超限会自动停用策略，防止低质量结果继续放行。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import (
    CollectionUnit,
    ResolutionStatus,
    ReviewDecision,
    ReviewPolicy,
    ReviewStatus,
    RiskLevel,
)
from .task_service import add_event

RISK_ORDER = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}


def _is_sampled(unit_id: str, rate: float) -> bool:
    """用单元 ID 稳定哈希映射到 [0,1)，使抽样不随进程改变。"""

    bucket = int(hashlib.sha256(unit_id.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return bucket < max(0.0, min(1.0, rate))


def apply_published_policy(db: Session, unit: CollectionUnit) -> bool:
    """尝试应用最新已发布策略，满足全部门禁时自动通过或进入抽样。"""

    policy = db.scalar(
        select(ReviewPolicy)
        .where(ReviewPolicy.status == "PUBLISHED")
        .order_by(desc(ReviewPolicy.published_at), desc(ReviewPolicy.version))
        .limit(1)
    )
    if policy is None or unit.resolution_status != ResolutionStatus.RESOLVED:
        return False
    rules = policy.rules or {}
    datasets = rules.get("datasets") or []
    if datasets and unit.record.sheet_name not in datasets:
        return False
    max_risk = RiskLevel(rules.get("max_risk", RiskLevel.LOW))
    if RISK_ORDER[RiskLevel(unit.risk_level)] > RISK_ORDER[max_risk]:
        return False
    validation = unit.validation or {}
    if validation.get("valid") is not True or validation.get("evidence_approved") is not True:
        return False
    if validation.get("conflicts"):
        return False

    unit.review_policy_id = policy.id
    if _is_sampled(unit.id, policy.sample_rate):
        unit.review_sampled = True
        unit.review_required = True
        policy.sample_total += 1
        add_event(
            db,
            unit.task_id,
            "review.policy.sampled",
            {"unit_id": unit.id, "policy_id": policy.id, "version": policy.version},
        )
        return False

    unit.review_status = ReviewStatus.AUTO_APPROVED
    unit.review_required = False
    unit.final_values = unit.suggestion or {}
    unit.version += 1
    db.add(
        ReviewDecision(
            unit_id=unit.id,
            actor_id=policy.created_by,
            decision=ReviewStatus.AUTO_APPROVED,
            before_values=None,
            after_values=unit.final_values,
            comment="Published review policy",
            unit_version=unit.version,
            policy_id=policy.id,
            metadata_json={"policy_version": policy.version, "rules": rules},
        )
    )
    add_event(
        db,
        unit.task_id,
        "review.policy.auto_approved",
        {"unit_id": unit.id, "policy_id": policy.id, "version": policy.version},
    )
    return True


def record_sample_outcome(db: Session, unit: CollectionUnit, *, incorrect: bool) -> None:
    """累计抽样人工结论；错误率超限时停用策略。"""

    if not unit.review_sampled or not unit.review_policy_id:
        return
    policy = db.get(ReviewPolicy, unit.review_policy_id)
    if policy is None:
        return
    if incorrect:
        policy.sample_errors += 1
    error_rate = policy.sample_errors / max(1, policy.sample_total)
    if policy.status == "PUBLISHED" and error_rate > policy.max_sample_error_rate:
        policy.status = "DISABLED"
        add_event(
            db,
            unit.task_id,
            "review.policy.circuit_opened",
            {
                "policy_id": policy.id,
                "sample_total": policy.sample_total,
                "sample_errors": policy.sample_errors,
                "error_rate": error_rate,
                "disabled_at": datetime.now(UTC).isoformat(),
            },
        )
