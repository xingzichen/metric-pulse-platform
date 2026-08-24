from __future__ import annotations

from metric_pulse.dataset_profiles import (
    AI_ALGORITHM_COLLECTION_PROFILE,
    AI_ALGORITHM_COLLECTION_TARGET_FIELDS,
    FORBES_AI50_SOURCE_URL,
    GITHUB_TOP_REPOSITORIES_API_URL,
    GITHUB_TOP_REPOSITORIES_SOURCE_URL,
    TOP_LIST_AI_PROFILE,
    TOP_LIST_AI_TARGET_FIELDS,
    ai_algorithm_collection_analysis_fields,
    ai_algorithm_collection_row_contract,
    ai_index_analysis_fields,
    ai_index_row_contract,
    ai_index_unit_targets,
    excluded_sheet_policy,
    has_locked_dataset_profile,
    is_ai_algorithm_collection_sheet,
    is_ai_index_sheet,
    is_excluded_sheet,
    is_top_list_ai_sheet,
    top_list_ai_analysis_fields,
    top_list_ai_row_contract,
)

HEADERS = [
    "logic_id",
    "classification_level1",
    "classification_level2",
    "level",
    "region",
    "province",
    "city",
    "district",
    "other_region",
    "statistical_date",
    "scope",
    "industry",
    "industry_id",
    "index_name",
    "be_data",
    "be_unit",
    "data",
    "unit",
    "source_url",
]


def test_out_of_scope_sheet_policy_distinguishes_manual_and_external_owners() -> None:
    manual = {
        "腾讯研究院-AI速递(AI_news)": "ai_news",
        "GPU芯片性能(gpu_chip_performance)": "gpu_chip_performance",
        "全球ai人才(ai_person)": "ai_person",
        "全市总智算规模(ai_computing_power)": "ai_computing_power",
    }
    external = {
        "大模型备案(ai_model_permission)": "ai_model_permission",
        "地方网信部门生成式人工智能服务已登记信息(aigc_reg_i": "aigc_reg_i",
    }

    for name, sheet_id in manual.items():
        assert excluded_sheet_policy(name) == {
            "sheet_id": sheet_id,
            "code": "MANUAL_PROCESS_ONLY",
            "label": "依赖人工处理",
        }
        assert is_excluded_sheet(name) is True
        assert has_locked_dataset_profile(name, []) is True
    for name, sheet_id in external.items():
        assert excluded_sheet_policy(name) == {
            "sheet_id": sheet_id,
            "code": "EXTERNAL_AUTOMATION_OWNED",
            "label": "由既有自动采集程序处理",
        }
        assert is_excluded_sheet(name) is True
        assert has_locked_dataset_profile(name, []) is True

    assert excluded_sheet_policy("全球人工智能AI产品榜(ai_product_ranking)") is None


def test_ai_index_analysis_does_not_drop_source_unit_when_prefilled() -> None:
    profile = ai_index_analysis_fields(HEADERS)

    assert profile["target_fields"] == ["be_data", "be_unit", "data", "source_url"]
    assert profile["descriptor_fields"][:3] == ["index_name", "level", "region"]
    assert "district" in profile["descriptor_fields"]
    assert "other_region" in profile["descriptor_fields"]


def test_ai_index_contract_freezes_subject_and_all_nonblank_constraints() -> None:
    raw = {
        "index_name": "AI投资规模",
        "level": "国家级",
        "region": "中国",
        "province": "广东省",
        "city": "深圳市",
        "district": "南山区",
        "other_region": "粤港澳大湾区",
        "statistical_date": 2025,
        "scope": "year",
        "industry": "人工智能",
        "be_unit": "百万美元",
        "unit": "亿美元",
    }

    contract = ai_index_row_contract(
        sheet_name="人工智能指标库(ai_index)",
        source_row=10,
        raw_data=raw,
        headers=HEADERS,
    )

    assert contract["identity"] == {"index_name": "AI投资规模"}
    assert contract["standard_unit"] == "亿美元"
    assert contract["source_unit_hint"] == "百万美元"
    assert set(contract["required_matches"]) == {
        "index_name",
        "level",
        "region",
        "province",
        "city",
        "district",
        "other_region",
        "statistical_date",
        "scope",
        "industry",
    }


def test_ai_index_always_recollects_observation_and_only_requires_missing_source() -> None:
    with_source = {"be_unit": "位", "source_url": "https://example.com/data"}
    without_source = {"be_unit": "位", "source_url": None}

    assert ai_index_unit_targets(with_source, HEADERS) == ["be_data", "be_unit", "data"]
    assert ai_index_unit_targets(without_source, HEADERS) == [
        "be_data",
        "be_unit",
        "data",
        "source_url",
    ]


def test_ai_index_profile_detection_is_stable_for_visual_role_locking() -> None:
    assert is_ai_index_sheet("人工智能指标库(ai_index)", HEADERS) is True
    assert is_ai_index_sheet("任意旧名称", HEADERS) is True


ALGORITHM_HEADERS = [
    "logic_id",
    "collect_date",
    "rank",
    "name",
    "star",
    "star_unit",
    "update_time",
    "created_time",
    "source_department",
    "source_url",
    "update_frequency",
    "datasource_date",
    "collection_date",
    "data_type",
    "data_status",
]


def test_algorithm_collection_profile_has_complete_locked_targets() -> None:
    assert is_ai_algorithm_collection_sheet(
        "人工智能算法收藏(ai_algorithm_collectio", ALGORITHM_HEADERS
    )
    profile = ai_algorithm_collection_analysis_fields(ALGORITHM_HEADERS)

    assert profile["descriptor_fields"] == []
    assert profile["target_fields"] == list(AI_ALGORITHM_COLLECTION_TARGET_FIELDS)
    assert "update_time" not in profile["target_fields"]
    assert "created_time" not in profile["target_fields"]


def test_algorithm_collection_contract_freezes_one_rank_and_fixed_snapshot() -> None:
    snapshot_at = "2026-08-24T20:30:00+08:00"

    raw, contract, targets = ai_algorithm_collection_row_contract(
        sheet_name="人工智能算法收藏(ai_algorithm_collectio",
        source_row=8,
        rank=5,
        snapshot_at=snapshot_at,
        headers=ALGORITHM_HEADERS,
    )

    assert targets == list(AI_ALGORITHM_COLLECTION_TARGET_FIELDS)
    assert contract["profile"] == AI_ALGORITHM_COLLECTION_PROFILE
    assert contract["mode"] == "monthly_top10_append"
    assert contract["required_matches"] == ["rank"]
    assert contract["descriptors"] == {"rank": 5}
    assert contract["canonical_source_url"] == GITHUB_TOP_REPOSITORIES_SOURCE_URL
    assert contract["acquisition_url"] == GITHUB_TOP_REPOSITORIES_API_URL
    assert contract["fixed_values"] == {
        "collect_date": snapshot_at,
        "rank": 5,
        "star_unit": "k",
        "source_department": "Github",
        "update_frequency": "month",
        "datasource_date": snapshot_at,
        "collection_date": snapshot_at,
        "data_type": "采集",
        "data_status": "新增",
    }
    assert raw["source_url"] == GITHUB_TOP_REPOSITORIES_SOURCE_URL
    assert raw["update_time"] is None
    assert raw["created_time"] is None


TOP_LIST_HEADERS = [
    "logic_id",
    "rank_year",
    "company_name",
    "headquarter_location",
    "CEO",
    "financing_amount",
    "financing_amount_unit",
    "establish_date",
    "update_time",
    "created_time",
    "source",
    "source_url",
    "update_frequency",
    "datasource_date",
    "collection_date",
    "data_type",
    "data_status",
]


def test_top_list_ai_profile_locks_complete_annual_targets() -> None:
    assert is_top_list_ai_sheet("TOP50企业排名(top_list_ai)", TOP_LIST_HEADERS)
    profile = top_list_ai_analysis_fields(TOP_LIST_HEADERS)

    assert profile["descriptor_fields"] == []
    assert profile["target_fields"] == list(TOP_LIST_AI_TARGET_FIELDS)
    assert "update_time" not in profile["target_fields"]
    assert "created_time" not in profile["target_fields"]


def test_top_list_ai_contract_uses_internal_position_without_exporting_rank() -> None:
    raw, contract, targets = top_list_ai_row_contract(
        sheet_name="TOP50企业排名(top_list_ai)",
        source_row=203,
        list_position=1,
        snapshot_at="2026-08-24T22:00:00+08:00",
        rank_year=2026,
        headers=TOP_LIST_HEADERS,
        superseded_rows=list(range(153, 203)),
    )

    assert targets == list(TOP_LIST_AI_TARGET_FIELDS)
    assert contract["profile"] == TOP_LIST_AI_PROFILE
    assert contract["mode"] == "annual_top50_append"
    assert contract["descriptors"] == {"list_position": 1, "rank_year": 2026}
    assert contract["required_matches"] == ["list_position", "rank_year"]
    assert contract["canonical_source_url"] == FORBES_AI50_SOURCE_URL
    assert contract["superseded_rows"] == list(range(153, 203))
    assert raw["source_url"] == FORBES_AI50_SOURCE_URL
    assert raw["rank_year"] == 2026
    assert raw["update_time"] is None
    assert "rank" not in targets
