"""采集单元的业务解决状态判定。

这里只根据目标值、执行结果和验证信息判定解决状态；人工是否已核对由独立的
``review_status`` 表示。来源字段属于溯源信息，不应单独把一个没有业务值的单元判为已解决。
"""

from __future__ import annotations

from typing import Any

from .models import CollectionUnit, ResolutionStatus, RiskLevel, UnitStatus

VALIDATION_VERSION = "resolution-v1"
PROVENANCE_FIELDS = {"source", "source_url"}


def classify_resolution(
    *,
    execution_status: str,
    target_fields: list[str],
    values: dict[str, Any] | None,
    validation: dict[str, Any] | None,
) -> tuple[ResolutionStatus, str, RiskLevel]:
    """根据完整性和证据状态返回解决状态、机器可读原因与风险。

    执行未成功时不评估业务值；字段齐全但证据未通过仍是未解决/高风险。若任务只采来源
    字段，则用全部目标字段判断完整性。
    """

    if execution_status != UnitStatus.SUCCEEDED:
        return ResolutionStatus.NOT_EVALUATED, "EXECUTION_NOT_SUCCEEDED", RiskLevel.HIGH

    validation = validation or {}
    values = values or {}
    if validation.get("contract_valid") is False or validation.get("contract_incomplete") is True:
        return ResolutionStatus.INVALID, "ROW_CONTRACT_INVALID", RiskLevel.HIGH
    conflicts = validation.get("conflicts")
    if validation.get("conflict") is True or (isinstance(conflicts, list) and conflicts):
        return ResolutionStatus.CONFLICT, "EVIDENCE_CONFLICT", RiskLevel.HIGH

    conversion = validation.get("conversion")
    conversion_status = conversion.get("status") if isinstance(conversion, dict) else None
    if conversion_status in {"DIMENSION_MISMATCH", "INVALID_RESULT"}:
        return ResolutionStatus.INVALID, "UNIT_CONVERSION_INVALID", RiskLevel.HIGH
    if conversion_status in {"MISSING_SOURCE_UNIT", "NON_NUMERIC"}:
        return ResolutionStatus.UNRESOLVED, "RAW_OBSERVATION_INCOMPLETE", RiskLevel.HIGH

    valid_empty_fields = {
        str(field) for field in validation.get("valid_empty_fields", []) if isinstance(field, str)
    }
    value_fields = [
        field
        for field in target_fields
        if field not in PROVENANCE_FIELDS and field not in valid_empty_fields
    ]
    evaluated_fields = value_fields or list(target_fields)
    present = [field for field in evaluated_fields if values.get(field) not in (None, "")]
    if not present:
        return ResolutionStatus.UNRESOLVED, "NO_SUPPORTED_VALUE", RiskLevel.HIGH
    if len(present) < len(evaluated_fields):
        return ResolutionStatus.PARTIAL, "REQUIRED_FIELDS_MISSING", RiskLevel.HIGH
    if validation.get("valid") is not True:
        return ResolutionStatus.UNRESOLVED, "VALIDATION_FAILED", RiskLevel.HIGH

    evidence_approved = validation.get("evidence_approved")
    risk = (
        RiskLevel.HIGH
        if validation.get("model_conversion_fallback") is True
        else (
            RiskLevel.LOW
            if evidence_approved is True or validation.get("fixture") is True
            else RiskLevel.MEDIUM
        )
    )
    return ResolutionStatus.RESOLVED, "VALIDATED_COMPLETE", risk


def apply_resolution(unit: CollectionUnit) -> None:
    """把纯函数判定写回单元，并记录规则版本以支持未来重算。"""

    status, reason, risk = classify_resolution(
        execution_status=unit.status,
        target_fields=list(unit.target_fields),
        values=unit.suggestion,
        validation=unit.validation,
    )
    unit.resolution_status = status
    unit.resolution_reason = reason
    unit.risk_level = risk
    unit.review_required = True
    unit.validation_version = VALIDATION_VERSION
