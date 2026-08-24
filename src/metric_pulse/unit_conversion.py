"""版本化、确定性优先的单位换算。

所有程序规则都把单位映射到同一维度的基础倍率并使用 ``Decimal`` 计算。未知表达式可以交给
既有双阶段模型的转换候选，但缺值、非数值和已知维度冲突必须失败关闭，不能借模型猜测。
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

RULE_VERSION = "unit-registry-v1"


class ConversionStatus(StrEnum):
    """程序转换结果以及是否允许进入模型降级。"""

    CONVERTED = "CONVERTED"
    SAME_UNIT = "SAME_UNIT"
    UNSUPPORTED = "UNSUPPORTED"
    MISSING_SOURCE_UNIT = "MISSING_SOURCE_UNIT"
    MISSING_TARGET_UNIT = "MISSING_TARGET_UNIT"
    NON_NUMERIC = "NON_NUMERIC"
    DIMENSION_MISMATCH = "DIMENSION_MISMATCH"
    INVALID_RESULT = "INVALID_RESULT"


@dataclass(frozen=True, slots=True)
class UnitDefinition:
    canonical: str
    dimension: str
    factor: Decimal


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """可直接进入 validation JSON 和审核界面的转换审计。"""

    status: str
    mode: str
    source_value: int | float | str | None
    source_unit: str | None
    target_unit: str | None
    result: int | float | None = None
    normalized_source_unit: str | None = None
    normalized_target_unit: str | None = None
    factor: str | None = None
    formula: str | None = None
    rule_version: str = RULE_VERSION
    reason: str | None = None
    model_fallback_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(value: str) -> str:
    return re.sub(r"[\s_]+", "", value.strip()).casefold().replace("\uff05", "%")


_UNITS: dict[str, UnitDefinition] = {}


def _register(
    canonical: str,
    dimension: str,
    factor: str,
    *aliases: str,
) -> None:
    definition = UnitDefinition(canonical, dimension, Decimal(factor))
    for value in (canonical, *aliases):
        _UNITS[_key(value)] = definition


# 现有 ai_index 表出现的货币数量级。不同货币维度彼此隔离，禁止无汇率证据换算。
for unit, factor, aliases in (
    ("美元", "1", ("USD", "US$", "$", "美金")),
    ("万美元", "1e4", ("万美金",)),
    ("百万美元", "1e6", ("million USD", "USD million", "百万美金")),
    ("亿美元", "1e8", ("亿美金",)),
    ("十亿美元", "1e9", ("billion USD", "USD billion")),
):
    _register(unit, "currency:USD", factor, *aliases)

for unit, factor, aliases in (
    ("元", "1", ("人民币元", "CNY", "RMB")),
    ("万元", "1e4", ("万元人民币",)),
    ("百万元", "1e6", ("百万人民币",)),
    ("千万元", "1e7", ()),
    ("亿元", "1e8", ("亿元人民币",)),
    ("十亿元", "1e9", ()),
):
    _register(unit, "currency:CNY", factor, *aliases)

# 中文计数单位按对象类型隔离，避免把“万人”与“万家”误认为可互换。
for base, dimension in (
    ("个", "count:item"),
    ("位", "count:person"),
    ("人", "count:person"),
    ("家", "count:organization"),
    ("块", "count:block"),
    ("枚", "count:piece"),
    ("次", "count:occurrence"),
):
    _register(base, dimension, "1")
    _register(f"万{base}", dimension, "1e4")
    _register(f"百万{base}", dimension, "1e6")
    _register(f"亿{base}", dimension, "1e8")

# 人与位在当前业务中都是人数计量，注册显式别名而不是依赖模糊字符串判断。
_register("位", "count:person", "1", "人")
_register("万位", "count:person", "1e4", "万人")
_register("百万位", "count:person", "1e6", "百万人")
_register("亿位", "count:person", "1e8", "亿人")

for prefix, exponent in (("", 0), ("K", 3), ("M", 6), ("G", 9), ("T", 12), ("P", 15), ("E", 18), ("Z", 21)):
    _register(f"{prefix}Flops", "compute:FLOPS", f"1e{exponent}", f"{prefix}FLOPS")
    _register(f"{prefix}B", "storage:byte", f"1e{exponent}")

_register("%", "ratio:percent", "1", "百分比", "percent")
_register("分", "score", "1")


def known_units() -> dict[str, dict[str, str]]:
    """返回去重后的规则快照，供测试和未来管理页面只读展示。"""

    return {
        definition.canonical: {
            "dimension": definition.dimension,
            "factor": str(definition.factor),
        }
        for definition in _UNITS.values()
    }


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    text = str(value).strip().replace(",", "").replace("\uff0c", "")
    if not text:
        return None
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value.normalize())


def convert_unit(value: Any, source_unit: Any, target_unit: Any) -> ConversionResult:
    """执行确定性单位换算，并只对真正未知的单位表达式开放模型降级。"""

    source_text = str(source_unit).strip() if source_unit not in (None, "") else None
    target_text = str(target_unit).strip() if target_unit not in (None, "") else None
    # 表中“指数/评分”等无量纲指标用两个空单位表示。两个单位同时为空是一个明确的
    # schema 状态，而不是缺失任意一侧单位；此时仍用 Decimal 做恒等转换并保留空单位导出。
    if not source_text and not target_text:
        numeric = _decimal(value)
        if numeric is None:
            return ConversionResult(
                ConversionStatus.NON_NUMERIC,
                "NONE",
                value,
                None,
                None,
                reason="无量纲指标的原始值不是有限数值",
            )
        result = _json_number(numeric)
        return ConversionResult(
            ConversionStatus.SAME_UNIT,
            "DETERMINISTIC",
            value,
            None,
            None,
            result=result,
            normalized_source_unit="无量纲",
            normalized_target_unit="无量纲",
            factor="1",
            formula=f"{numeric} x 1 = {numeric}",
            reason="来源单位和标准单位均为空 按无量纲指标恒等转换",
        )
    if not source_text:
        return ConversionResult(
            ConversionStatus.MISSING_SOURCE_UNIT,
            "NONE",
            value,
            None,
            target_text,
            reason="来源原始单位为空",
        )
    if not target_text:
        return ConversionResult(
            ConversionStatus.MISSING_TARGET_UNIT,
            "NONE",
            value,
            source_text,
            None,
            reason="标准目标单位为空",
        )
    numeric = _decimal(value)
    if numeric is None:
        return ConversionResult(
            ConversionStatus.NON_NUMERIC,
            "NONE",
            value,
            source_text,
            target_text,
            reason="原始值不是有限数值",
        )
    source = _UNITS.get(_key(source_text))
    target = _UNITS.get(_key(target_text))
    if source is None or target is None:
        return ConversionResult(
            ConversionStatus.UNSUPPORTED,
            "NONE",
            value,
            source_text,
            target_text,
            normalized_source_unit=source.canonical if source else None,
            normalized_target_unit=target.canonical if target else None,
            reason="单位注册表没有覆盖该转换",
            model_fallback_allowed=True,
        )
    if source.dimension != target.dimension:
        return ConversionResult(
            ConversionStatus.DIMENSION_MISMATCH,
            "NONE",
            value,
            source_text,
            target_text,
            normalized_source_unit=source.canonical,
            normalized_target_unit=target.canonical,
            reason=f"单位维度不一致: {source.dimension} → {target.dimension}",
        )
    factor = source.factor / target.factor
    converted = numeric * factor
    if not converted.is_finite():
        return ConversionResult(
            ConversionStatus.INVALID_RESULT,
            "DETERMINISTIC",
            value,
            source_text,
            target_text,
            normalized_source_unit=source.canonical,
            normalized_target_unit=target.canonical,
            factor=str(factor),
            reason="转换结果不是有限数值",
        )
    status = ConversionStatus.SAME_UNIT if factor == 1 else ConversionStatus.CONVERTED
    result = _json_number(converted)
    return ConversionResult(
        status,
        "DETERMINISTIC",
        value,
        source_text,
        target_text,
        result=result,
        normalized_source_unit=source.canonical,
        normalized_target_unit=target.canonical,
        factor=str(factor.normalize()),
        formula=f"{numeric} x {factor.normalize()} = {converted.normalize()}",
    )


def model_fallback_conversion(
    *,
    program_result: ConversionResult,
    verification: dict[str, Any],
) -> ConversionResult | None:
    """验证 VERIFY 中的模型换算候选；不满足完整契约时返回 ``None``。

    该函数不判断事实证据是否批准，调用方必须先完成来源和约束门禁。
    """

    if (
        program_result.status != ConversionStatus.UNSUPPORTED
        or not program_result.model_fallback_allowed
    ):
        return None
    candidate = verification.get("conversion")
    if not isinstance(candidate, dict):
        return None
    if str(candidate.get("mode", "")).upper() != "MODEL_FALLBACK":
        return None
    source_value = _decimal(candidate.get("source_value"))
    expected_value = _decimal(program_result.source_value)
    result = _decimal(candidate.get("result"))
    if source_value is None or expected_value is None or source_value != expected_value or result is None:
        return None
    if _key(str(candidate.get("source_unit", ""))) != _key(program_result.source_unit or ""):
        return None
    if _key(str(candidate.get("target_unit", ""))) != _key(program_result.target_unit or ""):
        return None
    formula = str(candidate.get("formula", "")).strip()
    reason = str(candidate.get("reason", "")).strip()
    numeric_result = float(result)
    if not formula or not reason or not math.isfinite(numeric_result):
        return None
    return ConversionResult(
        ConversionStatus.CONVERTED,
        "MODEL_FALLBACK",
        program_result.source_value,
        program_result.source_unit,
        program_result.target_unit,
        result=_json_number(result),
        formula=formula,
        reason=reason,
        model_fallback_allowed=True,
    )
