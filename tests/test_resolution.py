from __future__ import annotations

from metric_pulse.models import ResolutionStatus, RiskLevel, UnitStatus
from metric_pulse.resolution import classify_resolution


def test_successful_execution_with_null_values_is_not_resolved() -> None:
    status, reason, risk = classify_resolution(
        execution_status=UnitStatus.SUCCEEDED,
        target_fields=["value", "source_url"],
        values={"value": None, "source_url": "https://example.com"},
        validation={"valid": False, "missing_fields": ["value"]},
    )
    assert status == ResolutionStatus.UNRESOLVED
    assert reason == "NO_SUPPORTED_VALUE"
    assert risk == RiskLevel.HIGH


def test_complete_validated_result_is_resolved() -> None:
    status, reason, risk = classify_resolution(
        execution_status=UnitStatus.SUCCEEDED,
        target_fields=["value", "source_url"],
        values={"value": 42, "source_url": "https://example.com"},
        validation={"valid": True, "evidence_approved": True},
    )
    assert status == ResolutionStatus.RESOLVED
    assert reason == "VALIDATED_COMPLETE"
    assert risk == RiskLevel.LOW


def test_execution_failure_is_not_evaluated() -> None:
    status, _, _ = classify_resolution(
        execution_status=UnitStatus.FAILED_RETRYABLE,
        target_fields=["value"],
        values=None,
        validation=None,
    )
    assert status == ResolutionStatus.NOT_EVALUATED


def test_unitless_ai_index_can_resolve_with_an_intentionally_blank_raw_unit() -> None:
    status, reason, risk = classify_resolution(
        execution_status=UnitStatus.SUCCEEDED,
        target_fields=["be_data", "be_unit", "data"],
        values={"be_data": 95, "be_unit": None, "data": 95},
        validation={
            "valid": True,
            "evidence_approved": True,
            "valid_empty_fields": ["be_unit"],
            "conversion": {
                "status": "SAME_UNIT",
                "mode": "DETERMINISTIC",
                "normalized_source_unit": "无量纲",
                "normalized_target_unit": "无量纲",
            },
        },
    )

    assert status == ResolutionStatus.RESOLVED
    assert reason == "VALIDATED_COMPLETE"
    assert risk == "LOW"
