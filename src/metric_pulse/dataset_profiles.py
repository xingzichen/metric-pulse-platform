"""需要稳定业务语义的工作表专用配置。

通用工作簿识别适合未知文件，但部分工作表的字段角色或处理边界已经由业务明确。本模块集中
定义专用采集契约和平台排除清单，避免前端配置、视觉识别或抽样空值率重新引入旧语义。
"""

from __future__ import annotations

from typing import Any

GITHUB_TOP_REPOSITORIES_SOURCE_URL = (
    "https://github.com/search?q=stars%3A%3E9999&type=repositories&s=stars&o=desc"
)
GITHUB_TOP_REPOSITORIES_API_URL = (
    "https://api.github.com/search/repositories?"
    "q=stars%3A%3E9999&sort=stars&order=desc&per_page=10&page=1"
)
FORBES_AI50_SOURCE_URL = "https://www.forbes.com/lists/ai50/"

# 以下机器名是业务边界，不是临时关闭开关。人工表由人工维护，外部程序表由既有自动程序
# 维护；两类表都不得进入本平台的规划、来源获取、模型、审核或导出写入链路。
MANUAL_ONLY_SHEET_IDS = frozenset(
    {
        "ai_news",
        "gpu_chip_performance",
        "ai_person",
        "ai_computing_power",
    }
)
EXTERNAL_AUTOMATION_SHEET_IDS = frozenset({"ai_model_permission", "aigc_reg_i"})

AI_INDEX_IDENTITY_FIELDS = ("index_name",)
AI_INDEX_CONSTRAINT_FIELDS = (
    "level",
    "region",
    "province",
    "city",
    "district",
    "other_region",
    "statistical_date",
    "scope",
    "industry",
)
AI_INDEX_CONTEXT_FIELDS = (
    "classification_level1",
    "classification_level2",
    "industry_id",
)
AI_INDEX_OBSERVED_FIELDS = ("be_data", "be_unit")
AI_INDEX_DERIVED_FIELDS = ("data",)
AI_INDEX_STANDARD_UNIT_FIELD = "unit"
AI_INDEX_PROVENANCE_FIELD = "source_url"

AI_ALGORITHM_COLLECTION_PROFILE = "ai_algorithm_collection_monthly_v1"
AI_ALGORITHM_COLLECTION_TOP_N = 10
AI_ALGORITHM_COLLECTION_OBSERVED_FIELDS = ("name", "star")
AI_ALGORITHM_COLLECTION_FIXED_FIELDS = (
    "collect_date",
    "rank",
    "star_unit",
    "source_department",
    "update_frequency",
    "datasource_date",
    "collection_date",
    "data_type",
    "data_status",
)
AI_ALGORITHM_COLLECTION_TARGET_FIELDS = (
    "logic_id",
    *AI_ALGORITHM_COLLECTION_FIXED_FIELDS[:2],
    *AI_ALGORITHM_COLLECTION_OBSERVED_FIELDS,
    *AI_ALGORITHM_COLLECTION_FIXED_FIELDS[2:4],
    "source_url",
    *AI_ALGORITHM_COLLECTION_FIXED_FIELDS[4:],
)

TOP_LIST_AI_PROFILE = "top_list_ai_forbes_annual_v1"
TOP_LIST_AI_COUNT = 50
TOP_LIST_AI_OBSERVED_FIELDS = (
    "company_name",
    "headquarter_location",
    "CEO",
    "financing_amount",
    "establish_date",
)
TOP_LIST_AI_APPLICATION_FIELDS = (
    "logic_id",
    "rank_year",
    "financing_amount_unit",
    "source",
    "source_url",
    "update_frequency",
    "datasource_date",
    "collection_date",
    "data_type",
    "data_status",
)
TOP_LIST_AI_TARGET_FIELDS = (
    "logic_id",
    "rank_year",
    *TOP_LIST_AI_OBSERVED_FIELDS[:4],
    "financing_amount_unit",
    TOP_LIST_AI_OBSERVED_FIELDS[4],
    "source",
    "source_url",
    "update_frequency",
    "datasource_date",
    "collection_date",
    "data_type",
    "data_status",
)


def is_ai_index_sheet(name: str, headers: list[str] | tuple[str, ...] = ()) -> bool:
    """识别人工智能指标库，同时允许机器名或稳定字段集合触发。"""

    normalized = name.casefold()
    required = {"index_name", "be_data", "be_unit", "data", "unit"}
    return "ai_index" in normalized or required.issubset(headers)


def is_ai_algorithm_collection_sheet(
    name: str,
    headers: list[str] | tuple[str, ...] = (),
) -> bool:
    """识别 GitHub 人工智能算法收藏月度榜单。"""

    normalized = name.casefold()
    required = {"collect_date", "rank", "name", "star", "star_unit", "source_url"}
    return "ai_algorithm_collectio" in normalized or (
        "算法收藏" in name and required.issubset(headers)
    )


def is_top_list_ai_sheet(
    name: str,
    headers: list[str] | tuple[str, ...] = (),
) -> bool:
    """识别福布斯 AI 50 年度增量表。"""

    normalized = name.casefold()
    required = {
        "rank_year",
        "company_name",
        "headquarter_location",
        "CEO",
        "financing_amount",
        "financing_amount_unit",
        "establish_date",
        "source_url",
    }
    return "top_list_ai" in normalized or ("top50" in normalized and required.issubset(headers))


def excluded_sheet_policy(name: str) -> dict[str, str] | None:
    """返回平台不处理工作表的稳定业务原因。

    Excel 标题可能因 31 字符上限而截断中文或右括号，因此只匹配不会被截断的机器名片段。
    返回值进入分析 JSON 和审计视图；不存在匹配时返回 ``None``。
    """

    normalized = name.casefold()
    for sheet_id in sorted(MANUAL_ONLY_SHEET_IDS):
        if sheet_id in normalized:
            return {
                "sheet_id": sheet_id,
                "code": "MANUAL_PROCESS_ONLY",
                "label": "依赖人工处理",
            }
    for sheet_id in sorted(EXTERNAL_AUTOMATION_SHEET_IDS):
        if sheet_id in normalized:
            return {
                "sheet_id": sheet_id,
                "code": "EXTERNAL_AUTOMATION_OWNED",
                "label": "由既有自动采集程序处理",
            }
    return None


def is_excluded_sheet(name: str) -> bool:
    """判断工作表是否位于本平台处理范围之外。"""

    return excluded_sheet_policy(name) is not None


def has_locked_dataset_profile(name: str, headers: list[str]) -> bool:
    """业务字段角色已明确的数据集禁止视觉识别覆盖其 profile。"""

    return (
        is_excluded_sheet(name)
        or is_ai_index_sheet(name, headers)
        or is_ai_algorithm_collection_sheet(name, headers)
        or is_top_list_ai_sheet(name, headers)
    )


def available(fields: tuple[str, ...], headers: list[str]) -> list[str]:
    """保持业务定义顺序并忽略当前工作表不存在的字段。"""

    return [field for field in fields if field in headers]


def ai_index_analysis_fields(headers: list[str]) -> dict[str, list[str]]:
    """返回工作簿分析和任务创建页面必须展示的 ai_index 字段建议。"""

    descriptors = available(
        AI_INDEX_IDENTITY_FIELDS + AI_INDEX_CONSTRAINT_FIELDS + AI_INDEX_CONTEXT_FIELDS,
        headers,
    )
    targets = available(
        AI_INDEX_OBSERVED_FIELDS + AI_INDEX_DERIVED_FIELDS + (AI_INDEX_PROVENANCE_FIELD,),
        headers,
    )
    keys = ["logic_id"] if "logic_id" in headers else []
    return {
        "descriptor_fields": descriptors,
        "target_fields": targets,
        "business_key_fields": keys,
    }


def ai_algorithm_collection_analysis_fields(headers: list[str]) -> dict[str, list[str]]:
    """返回月度 GitHub 前十榜单的固定字段角色。"""

    return {
        "descriptor_fields": [],
        "target_fields": available(AI_ALGORITHM_COLLECTION_TARGET_FIELDS, headers),
        "business_key_fields": ["logic_id"] if "logic_id" in headers else [],
    }


def top_list_ai_analysis_fields(headers: list[str]) -> dict[str, list[str]]:
    """返回福布斯年度 AI 50 的完整锁定字段角色。"""

    return {
        "descriptor_fields": [],
        "target_fields": available(TOP_LIST_AI_TARGET_FIELDS, headers),
        "business_key_fields": ["logic_id"] if "logic_id" in headers else [],
    }


def ai_algorithm_collection_row_contract(
    *,
    sheet_name: str,
    source_row: int,
    rank: int,
    snapshot_at: str,
    headers: list[str],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """为一个 GitHub 榜单名次生成待追加行及不可变采集契约。

    榜单时间在任务规划时一次性冻结，确保十行即使耗时不同也属于同一个月度快照。名称和
    收藏量来自 GitHub；其他字段由应用确定性生成，模型无权覆盖。
    """

    fixed_values = {
        "collect_date": snapshot_at,
        "rank": rank,
        "star_unit": "k",
        "source_department": "Github",
        "source_url": GITHUB_TOP_REPOSITORIES_SOURCE_URL,
        "update_frequency": "month",
        "datasource_date": snapshot_at,
        "collection_date": snapshot_at,
        "data_type": "采集",
        "data_status": "新增",
    }
    raw_data = {header: None for header in headers}
    raw_data.update({field: value for field, value in fixed_values.items() if field in headers})
    target_fields = available(AI_ALGORITHM_COLLECTION_TARGET_FIELDS, headers)
    contract = {
        "sheet_name": sheet_name,
        "source_row": source_row,
        "descriptors": {"rank": rank},
        "required_matches": ["rank"],
        "target_fields": target_fields,
        "field_roles": {
            "observed": available(AI_ALGORITHM_COLLECTION_OBSERVED_FIELDS, headers),
            "application_owned": [
                field
                for field in ("logic_id", *AI_ALGORITHM_COLLECTION_FIXED_FIELDS)
                if field in headers
            ],
            "provenance": "source_url",
        },
        "fixed_values": {
            field: value
            for field, value in fixed_values.items()
            if field != "source_url" and field in headers
        },
        "canonical_source_url": GITHUB_TOP_REPOSITORIES_SOURCE_URL,
        "acquisition_url": GITHUB_TOP_REPOSITORIES_API_URL,
        "rank": rank,
        "snapshot_at": snapshot_at,
        "star_transform": "floor(stargazers_count / 1000)",
        "mode": "monthly_top10_append",
        "profile": AI_ALGORITHM_COLLECTION_PROFILE,
    }
    return raw_data, contract, target_fields


def top_list_ai_row_contract(
    *,
    sheet_name: str,
    source_row: int,
    list_position: int,
    snapshot_at: str,
    rank_year: int,
    headers: list[str],
    superseded_rows: list[int],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """为福布斯年度名单中的一个内部位置生成独立待追加行契约。

    ``list_position`` 只用于从官方按字母排序的 50 家公司中切出当前证据，不是福布斯排名，
    也没有对应的工作簿输出字段。
    """

    fixed_values = {
        "rank_year": rank_year,
        "financing_amount_unit": "亿美元",
        "source": "福布斯",
        "source_url": FORBES_AI50_SOURCE_URL,
        "update_frequency": "year",
        "collection_date": snapshot_at,
        "data_type": "采集",
        "data_status": "新增",
    }
    raw_data = {header: None for header in headers}
    raw_data.update({field: value for field, value in fixed_values.items() if field in headers})
    target_fields = available(TOP_LIST_AI_TARGET_FIELDS, headers)
    contract = {
        "sheet_name": sheet_name,
        "source_row": source_row,
        "descriptors": {"list_position": list_position, "rank_year": rank_year},
        "required_matches": ["list_position", "rank_year"],
        "target_fields": target_fields,
        "field_roles": {
            "observed": available(TOP_LIST_AI_OBSERVED_FIELDS, headers),
            "application_owned": available(TOP_LIST_AI_APPLICATION_FIELDS, headers),
            "provenance": "source_url",
        },
        "fixed_values": {
            field: value
            for field, value in fixed_values.items()
            if field != "source_url" and field in headers
        },
        "canonical_source_url": FORBES_AI50_SOURCE_URL,
        "acquisition_url": FORBES_AI50_SOURCE_URL,
        "list_position": list_position,
        "snapshot_at": snapshot_at,
        "rank_year": rank_year,
        "superseded_rows": list(superseded_rows),
        "batch_key": f"{rank_year}:{FORBES_AI50_SOURCE_URL}",
        "mode": "annual_top50_append",
        "profile": TOP_LIST_AI_PROFILE,
    }
    return raw_data, contract, target_fields


def ai_index_row_contract(
    *,
    sheet_name: str,
    source_row: int,
    raw_data: dict[str, Any],
    headers: list[str],
) -> dict[str, Any]:
    """冻结 ai_index 主体、联合约束、辅助上下文和字段角色。

    ``source_unit_hint`` 仅帮助检索，不能作为已采集的 ``be_unit`` 输出。联合约束只要求
    当前行非空的字段；空单元格表示“未指定”，不会被当成任意值的肯定证据。
    """

    identity_fields = available(AI_INDEX_IDENTITY_FIELDS, headers)
    constraint_fields = available(AI_INDEX_CONSTRAINT_FIELDS, headers)
    context_fields = available(AI_INDEX_CONTEXT_FIELDS, headers)
    identity = {field: raw_data.get(field) for field in identity_fields}
    constraints = {field: raw_data.get(field) for field in constraint_fields}
    context = {field: raw_data.get(field) for field in context_fields}
    required_matches = [
        field
        for field in identity_fields + constraint_fields
        if raw_data.get(field) not in (None, "")
    ]
    return {
        "sheet_name": sheet_name,
        "source_row": source_row,
        "identity": identity,
        "constraints": constraints,
        "context": context,
        "descriptors": {**identity, **constraints, **context},
        "required_matches": required_matches,
        "target_fields": available(
            AI_INDEX_OBSERVED_FIELDS + AI_INDEX_DERIVED_FIELDS + (AI_INDEX_PROVENANCE_FIELD,),
            headers,
        ),
        "field_roles": {
            "observed": available(AI_INDEX_OBSERVED_FIELDS, headers),
            "derived": available(AI_INDEX_DERIVED_FIELDS, headers),
            "standard_unit": AI_INDEX_STANDARD_UNIT_FIELD,
            "provenance": AI_INDEX_PROVENANCE_FIELD,
        },
        "standard_unit": raw_data.get(AI_INDEX_STANDARD_UNIT_FIELD),
        "source_unit_hint": raw_data.get("be_unit"),
        "mode": "row_contract_collect",
        "profile": "ai_index_v1",
    }


def ai_index_unit_targets(raw_data: dict[str, Any], headers: list[str]) -> list[str]:
    """返回本行原子输出组；无输入链接时把来源 URL 一并设为必需输出。"""

    targets = available(AI_INDEX_OBSERVED_FIELDS + AI_INDEX_DERIVED_FIELDS, headers)
    if (
        AI_INDEX_PROVENANCE_FIELD in headers
        and raw_data.get(AI_INDEX_PROVENANCE_FIELD) in (None, "")
    ):
        targets.append(AI_INDEX_PROVENANCE_FIELD)
    return targets
