from __future__ import annotations

from metric_pulse.unit_conversion import (
    ConversionStatus,
    convert_unit,
    known_units,
    model_fallback_conversion,
)


def test_existing_ai_index_units_are_covered_by_registry() -> None:
    units = known_units()
    for unit in {
        "位",
        "个",
        "分",
        "%",
        "亿元",
        "十亿美元",
        "次",
        "EFlops",
        "家",
        "枚",
        "亿美元",
        "百万个",
        "百万美元",
        "万块",
        "ZB",
        "万家",
        "十亿元",
        "ZFlops",
    }:
        assert unit in units


def test_million_usd_to_hundred_million_usd_is_deterministic() -> None:
    result = convert_unit(500, "百万美元", "亿美元")

    assert result.status == ConversionStatus.CONVERTED
    assert result.mode == "DETERMINISTIC"
    assert result.factor == "0.01"
    assert result.result == 5


def test_zflops_to_eflops_is_deterministic() -> None:
    result = convert_unit(2, "ZFlops", "EFlops")

    assert result.status == ConversionStatus.CONVERTED
    assert result.factor == "1E+3"
    assert result.result == 2000


def test_same_registered_unit_preserves_original_value() -> None:
    result = convert_unit("1,250.5", "位", "人")

    assert result.status == ConversionStatus.SAME_UNIT
    assert result.result == 1250.5


def test_known_dimension_mismatch_cannot_fall_back_to_model() -> None:
    program = convert_unit(12, "亿美元", "EFlops")

    assert program.status == ConversionStatus.DIMENSION_MISMATCH
    assert program.model_fallback_allowed is False
    assert (
        model_fallback_conversion(
            program_result=program,
            verification={
                "conversion": {
                    "mode": "MODEL_FALLBACK",
                    "source_value": 12,
                    "source_unit": "亿美元",
                    "target_unit": "EFlops",
                    "result": 12,
                    "formula": "guess",
                    "reason": "guess",
                }
            },
        )
        is None
    )


def test_unknown_unit_can_use_complete_verified_model_candidate() -> None:
    program = convert_unit(3, "兆样本", "亿样本")
    fallback = model_fallback_conversion(
        program_result=program,
        verification={
            "conversion": {
                "mode": "MODEL_FALLBACK",
                "source_value": 3,
                "source_unit": "兆样本",
                "target_unit": "亿样本",
                "result": 30000,
                "formula": "3 x 10000 = 30000",
                "reason": "兆与亿的十进制数量级换算",
            }
        },
    )

    assert program.status == ConversionStatus.UNSUPPORTED
    assert fallback is not None
    assert fallback.mode == "MODEL_FALLBACK"
    assert fallback.result == 30000


def test_missing_target_unit_does_not_allow_model_guess() -> None:
    result = convert_unit(10, "位", None)

    assert result.status == ConversionStatus.MISSING_TARGET_UNIT
    assert result.model_fallback_allowed is False


def test_two_blank_units_are_a_deterministic_unitless_identity_conversion() -> None:
    result = convert_unit("95", None, None)

    assert result.status == ConversionStatus.SAME_UNIT
    assert result.mode == "DETERMINISTIC"
    assert result.result == 95
    assert result.normalized_source_unit == "无量纲"
    assert result.normalized_target_unit == "无量纲"
    assert result.model_fallback_allowed is False
