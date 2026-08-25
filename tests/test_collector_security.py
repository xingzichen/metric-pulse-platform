from __future__ import annotations

import asyncio
import json
import socket

import pytest

from metric_pulse.collector import (
    CollectionResult,
    EvidenceItem,
    OMLXCollector,
    _normalize_image_table_response,
    apply_ai_index_conversion,
    apply_source_provenance,
    apply_verification,
    build_search_query,
    enrich_document_image_tables,
    extract_structured_values,
    focus_evidence,
    render_search_evidence,
    render_source_documents,
    structured_match_diagnostics,
    validate_public_url,
)
from metric_pulse.config import get_settings
from metric_pulse.dataset_profiles import (
    AI_ALGORITHM_COLLECTION_TARGET_FIELDS,
    GITHUB_TOP_REPOSITORIES_API_URL,
    GITHUB_TOP_REPOSITORIES_SOURCE_URL,
    ai_algorithm_collection_row_contract,
)
from metric_pulse.models import CollectionUnit, DataRecord
from metric_pulse.omlx import OMLXClient, OMLXError
from metric_pulse.processor import validate_production_collection_contract
from metric_pulse.source_pipeline import ImageEvidence, SourceDocument


def _address(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (ip, port))


def test_ai_index_program_conversion_overrides_model_data_candidate() -> None:
    values, conversion = apply_ai_index_conversion(
        values={"be_data": 500, "be_unit": "百万美元", "data": 999},
        row_contract={
            "standard_unit": "亿美元",
            "field_roles": {"derived": ["data"]},
        },
        verification={"conversion": {"result": 999}},
        evidence_approved=True,
    )

    assert values["data"] == 5
    assert conversion["mode"] == "DETERMINISTIC"
    assert conversion["factor"] == "0.01"


def test_ai_index_unknown_unit_uses_only_complete_verify_fallback() -> None:
    values, conversion = apply_ai_index_conversion(
        values={"be_data": 3, "be_unit": "兆样本", "data": None},
        row_contract={
            "standard_unit": "亿样本",
            "field_roles": {"derived": ["data"]},
        },
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
        evidence_approved=True,
    )

    assert values["data"] == 30000
    assert conversion["mode"] == "MODEL_FALLBACK"


def test_configured_proxy_fake_ip_is_allowed_for_hostname(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ssrf_proxy_networks", "198.18.0.0/15")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: [_address("198.18.10.20")])

    asyncio.run(validate_public_url("https://example.com/source"))


@pytest.mark.parametrize("url", ["http://198.18.10.20/source", "http://10.0.0.5/source"])
def test_non_global_ip_literals_remain_blocked(monkeypatch, url: str) -> None:
    monkeypatch.setattr(get_settings(), "ssrf_proxy_networks", "198.18.0.0/15")

    with pytest.raises(ValueError, match="Private, loopback, and reserved"):
        asyncio.run(validate_public_url(url))


def test_unconfigured_private_resolution_remains_blocked(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "ssrf_proxy_networks", "198.18.0.0/15")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args: [_address("10.0.0.5")])

    with pytest.raises(ValueError, match="Private, loopback, and reserved"):
        asyncio.run(validate_public_url("https://internal.example/source"))


def test_focus_evidence_finds_csv_target_beyond_initial_window() -> None:
    rows = ["developers,iso2_code,year,quarter"]
    rows.extend(f"{index},US,2020,1" for index in range(5_000))
    rows.append("12345,BY,2025,2")

    focused = focus_evidence(
        "\n".join(rows),
        {"region": "BY", "statistical_date": "2025Q2"},
    )

    assert focused.startswith("developers,iso2_code,year,quarter")
    assert "12345,BY,2025,2" in focused
    assert len(focused) <= 6_000


def test_extract_structured_values_maps_single_metric_column() -> None:
    source = "developers,iso2_code,year,quarter\n358848,BY,2025,2"

    extracted = extract_structured_values(
        source,
        {"region": "BY", "statistical_date": "2025Q2"},
        ["be_data"],
    )

    assert extracted == (
        {"be_data": 358848},
        "developers,iso2_code,year,quarter\n358848,BY,2025,2",
    )


def test_extract_structured_values_rejects_ambiguous_matches() -> None:
    source = "developers,iso2_code,year,quarter\n1,BY,2025,2\n2,BY,2025,2"

    assert (
        extract_structured_values(
            source,
            {"region": "BY", "statistical_date": "2025Q2"},
            ["be_data", "data"],
        )
        is None
    )


def test_github_innovation_graph_ec_2021q3_is_uniquely_matched() -> None:
    source = "developers,iso2_code,year,quarter\n125033,EC,2021,3\n7637259,CN,2021,3\n"
    descriptors = {
        "region": "EC",
        "statistical_date": "2021Q3",
        "index_name": "Github开发者数量",
    }

    diagnostics = structured_match_diagnostics(source, descriptors)
    extracted = extract_structured_values(source, descriptors, ["be_data"])

    assert diagnostics["status"] == "UNIQUE_MATCH"
    assert diagnostics["match_count"] == 1
    assert extracted == (
        {"be_data": 125033},
        "developers,iso2_code,year,quarter\n125033,EC,2021,3",
    )


def test_ai_index_empty_workbook_rows_6892_to_6894_match_world_bank_series() -> None:
    source = (
        "country,country_id,date,value,decimal\n"
        "美国,US,2018,2.98956,2\n美国,US,2019,3.14297,2\n美国,US,2020,3.41788,2\n"
    )

    for period, expected in ((2018, 2.98956), (2019, 3.14297), (2020, 3.41788)):
        descriptors = {"region": "美国", "statistical_date": period}
        assert structured_match_diagnostics(source, descriptors)["status"] == "UNIQUE_MATCH"
        assert extract_structured_values(source, descriptors, ["be_data"]) == (
            {"be_data": expected},
            "country,country_id,date,value,decimal\n"
            + next(line for line in source.splitlines() if f",{period}," in line),
        )


def test_focus_evidence_strips_html_and_keeps_matching_context() -> None:
    source = "<html><script>ignore me</script><body>" + ("noise " * 2_000) + "深圳 智算规模 42 PFLOPS</body>"

    focused = focus_evidence(source, {"city": "深圳", "project": "智算规模"})

    assert "ignore me" not in focused
    assert "深圳 智算规模 42 PFLOPS" in focused
    assert len(focused) <= 6_000


def test_render_search_evidence_preserves_titles_urls_and_snippets() -> None:
    rendered = render_search_evidence(
        [
            EvidenceItem(
                source_url="https://example.com/report",
                title="AI Index",
                excerpt="Sweden created 42 AI companies in 2025.",
            )
        ]
    )

    assert "AI Index" in rendered
    assert "https://example.com/report" in rendered
    assert "42 AI companies" in rendered


def test_apply_source_provenance_fills_only_missing_targets() -> None:
    values = apply_source_provenance(
        {"data": 42, "source": None, "source_url": None},
        ["data", "source", "source_url"],
        source_url="https://example.com/report",
        source_title="Example report",
    )

    assert values == {
        "data": 42,
        "source": "Example report",
        "source_url": "https://example.com/report",
    }


def test_search_query_prioritizes_metric_location_date_and_unit() -> None:
    record = DataRecord(
        sheet_name="人工智能指标库\uff08ai_index\uff09",
        source_row=80,
        business_key="sample",
        raw_data={"unit": "亿元"},
        row_contract={
            "descriptors": {
                "classification_level1": "产业规模与市场规模",
                "region": "中国",
                "province": "天津市",
                "city": "天津市",
                "statistical_date": 2023,
                "industry": "人工智能",
                "index_name": "AI存储产业规模",
            }
        },
    )

    query = build_search_query(record)

    assert query.startswith('"AI存储产业规模"')
    assert "天津市" in query
    assert "2023" in query
    assert "亿元" in query
    assert "产业规模与市场规模" not in query


def test_unapproved_verification_keeps_candidate_url_out_of_target_values() -> None:
    values, approved = apply_verification(
        {
            "approved": False,
            "values": {"data": 124, "source_url": "https://example.com/report"},
        },
        ["data", "source_url"],
        ["data"],
    )

    assert approved is False
    assert values == {"data": None, "source_url": None}


def test_structured_source_still_runs_two_independent_qwen_calls(monkeypatch) -> None:
    calls: list[str] = []

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            calls.append(prompt)
            if len(calls) == 1:
                return {
                    "values": {"data": 42},
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                }
            return {
                "approved": True,
                "values": {
                    "data": 42,
                    "source_url": "https://model.invalid/invented",
                },
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
                "reason": "direct structured evidence",
            }

    async def fake_search(_query, *, limit):
        assert limit == 10
        return [EvidenceItem(source_url="https://example.com/data.csv", title="Official data")]

    async def fake_gather(*_args, **_kwargs):
        return [
            SourceDocument(
                index=1,
                url="https://example.com/data.csv",
                title="Official data",
                media_type="text/csv",
                text="region,data\nChina,42",
            )
        ]

    monkeypatch.setattr("metric_pulse.collector.discover_sources", fake_search)
    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    record = DataRecord(
        sheet_name="Sheet1",
        source_row=2,
        business_key="row",
        raw_data={},
        row_contract={"descriptors": {"region": "China"}},
    )
    unit = CollectionUnit(target_fields=["data", "source_url"])

    result = asyncio.run(OMLXCollector(FakeClient()).collect(record, unit))

    assert result.values == {
        "data": 42,
        "source_url": "https://example.com/data.csv",
    }
    assert len(calls) == 2
    assert [item["phase"] for item in result.model_calls] == ["SYNTHESIZE", "VERIFY"]
    assert result.search_attempt["result_count"] == 1
    assert result.acquisition_attempt["route"] == "SEARCH_FALLBACK"
    assert result.acquisition_attempt["reason"] == "NO_DIRECT_SOURCE"
    assert "deterministic_structured_candidates" in calls[0]


def test_failed_verification_keeps_search_url_in_evidence_only(monkeypatch) -> None:
    calls = 0

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "values": {"data": 42},
                    "source_indices": [1],
                    "confidence": 0.4,
                    "conflicts": [],
                }
            return {
                "approved": False,
                "values": {
                    "data": 42,
                    "source_url": "https://example.com/candidate",
                },
                "source_indices": [1],
                "confidence": 0.2,
                "conflicts": [],
                "reason": "period mismatch",
            }

    async def fake_search(_query, *, limit):
        return [EvidenceItem(source_url="https://example.com/candidate", title="Candidate")]

    async def fake_gather(*_args, **_kwargs):
        return [
            SourceDocument(
                index=1,
                url="https://example.com/candidate",
                title="Candidate",
                media_type="text/html",
                text="A nearby but wrong-period value is 42.",
            )
        ]

    monkeypatch.setattr("metric_pulse.collector.discover_sources", fake_search)
    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    result = asyncio.run(
        OMLXCollector(FakeClient()).collect(
            DataRecord(
                sheet_name="Sheet1",
                source_row=2,
                business_key="row",
                raw_data={},
                row_contract={"descriptors": {"region": "EC", "statistical_date": "2021Q3"}},
            ),
            CollectionUnit(target_fields=["data", "source_url"]),
        )
    )

    assert result.values == {"data": None, "source_url": None}
    assert result.validation["valid"] is False
    assert any(item.source_url == "https://example.com/candidate" for item in result.evidence)


def test_approved_verification_without_cited_source_cannot_fill_source_url(monkeypatch) -> None:
    calls = 0

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"values": {"data": 42}, "source_indices": [1]}
            return {
                "approved": True,
                "values": {
                    "data": 42,
                    "source_url": "https://model.invalid/invented",
                },
                "source_indices": [],
                "confidence": 0.99,
                "conflicts": [],
                "reason": "claimed approval without a citation",
            }

    async def fake_search(_query, *, limit):
        return [EvidenceItem(source_url="https://example.com/result", title="Result")]

    async def fake_gather(*_args, **_kwargs):
        return [
            SourceDocument(
                index=1,
                url="https://example.com/result",
                title="Result",
                media_type="text/html",
                text="The corresponding value is 42.",
            )
        ]

    monkeypatch.setattr("metric_pulse.collector.discover_sources", fake_search)
    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    result = asyncio.run(
        OMLXCollector(FakeClient()).collect(
            DataRecord(
                sheet_name="Sheet1",
                source_row=2,
                business_key="row",
                raw_data={},
                row_contract={"descriptors": {"metric": "example"}},
            ),
            CollectionUnit(target_fields=["data", "source_url"]),
        )
    )

    assert result.values == {"data": 42, "source_url": None}
    assert result.validation["valid"] is False
    assert result.validation["evidence_approved"] is True


def test_direct_structured_source_skips_search_and_still_runs_two_calls(monkeypatch) -> None:
    calls: list[str] = []

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            calls.append(prompt)
            if len(calls) == 1:
                return {
                    "values": {"be_data": 125033, "be_unit": "位", "data": 125033},
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                }
            return {
                "approved": True,
                "values": {"be_data": 125033, "be_unit": "位", "data": 125033},
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
                "constraint_matches": {
                    "index_name": True,
                    "region": True,
                    "statistical_date": True,
                },
                "reason": "exact EC 2021 Q3 row",
            }

    async def search_must_not_run(_query, *, limit):
        raise AssertionError("direct unique match must not call search")

    async def fake_gather(*_args, **_kwargs):
        return [
            SourceDocument(
                index=1,
                url="https://raw.githubusercontent.com/github/innovationgraph/main/data/developers.csv",
                requested_url=("https://github.com/github/innovationgraph/blob/main/data/developers.csv"),
                normalized_url=(
                    "https://raw.githubusercontent.com/github/innovationgraph/main/data/developers.csv"
                ),
                title="Workbook-provided source",
                media_type="text/csv",
                text=("developers,iso2_code,year,quarter\n125033,EC,2021,3\n7637259,CN,2021,3"),
                content_hash="a" * 64,
            )
        ]

    monkeypatch.setattr("metric_pulse.collector.discover_sources", search_must_not_run)
    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    record = DataRecord(
        sheet_name="人工智能指标库\uff08ai_index\uff09",
        source_row=1955,
        business_key="ai-index-1955",
        raw_data={"source_url": ("https://github.com/github/innovationgraph/blob/main/data/developers.csv")},
        row_contract={
            "profile": "ai_index_v1",
            "descriptors": {
                "region": "EC",
                "statistical_date": "2021Q3",
                "index_name": "Github开发者数量",
            },
            "required_matches": ["index_name", "region", "statistical_date"],
            "standard_unit": "位",
            "field_roles": {
                "observed": ["be_data", "be_unit"],
                "derived": ["data"],
                "standard_unit": "unit",
                "provenance": "source_url",
            },
        },
    )
    unit = CollectionUnit(target_fields=["be_data", "be_unit", "data"])

    result = asyncio.run(OMLXCollector(FakeClient()).collect(record, unit))

    assert result.values == {"be_data": 125033, "be_unit": "位", "data": 125033}
    assert len(calls) == 2
    assert result.search_attempt is None
    assert result.acquisition_attempt["route"] == "DIRECT_LINK"
    assert result.acquisition_attempt["match_status"] == "UNIQUE_MATCH"
    assert result.acquisition_attempt["match_count"] == 1
    assert all('"source_row": 1955' not in prompt for prompt in calls)


def test_ambiguous_direct_source_falls_back_to_one_row_search(monkeypatch) -> None:
    model_calls = 0
    searches = 0
    gathers = 0

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            nonlocal model_calls
            model_calls += 1
            if model_calls == 1:
                return {
                    "values": {"data": 42},
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                }
            return {
                "approved": True,
                "values": {"data": 42},
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
                "reason": "search fallback evidence",
            }

    async def fake_search(_query, *, limit):
        nonlocal searches
        searches += 1
        return [EvidenceItem(source_url="https://example.com/search-result", title="Result")]

    async def fake_gather(*_args, **_kwargs):
        nonlocal gathers
        gathers += 1
        if gathers == 1:
            return [
                SourceDocument(
                    index=1,
                    url="https://example.com/direct.csv",
                    media_type="text/csv",
                    text="region,data\nEC,41\nEC,42",
                )
            ]
        return [
            SourceDocument(
                index=1,
                url="https://example.com/search-result",
                media_type="text/html",
                text="The exact EC result is 42 in the requested period.",
            )
        ]

    monkeypatch.setattr("metric_pulse.collector.discover_sources", fake_search)
    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    record = DataRecord(
        sheet_name="Sheet1",
        source_row=2,
        business_key="ambiguous",
        raw_data={"source_url": "https://example.com/direct.csv"},
        row_contract={"descriptors": {"region": "EC"}},
    )

    result = asyncio.run(OMLXCollector(FakeClient()).collect(record, CollectionUnit(target_fields=["data"])))

    assert searches == 1
    assert gathers == 2
    assert model_calls == 2
    assert result.acquisition_attempt["route"] == "SEARCH_FALLBACK"
    assert result.acquisition_attempt["reason"] == "AMBIGUOUS_MATCH"
    assert result.search_attempt["result_count"] == 1


def test_production_contract_accepts_direct_or_search_route_but_not_both() -> None:
    calls = [
        {"phase": "SYNTHESIZE", "model": "Qwen3.8-27B-6bit"},
        {"phase": "VERIFY", "model": "Qwen3.8-27B-6bit"},
    ]
    direct = CollectionResult(
        values={"data": 1},
        acquisition_attempt={"route": "DIRECT_LINK", "status": "SUCCEEDED"},
        model_calls=calls,
    )
    fallback = CollectionResult(
        values={"data": 1},
        acquisition_attempt={"route": "SEARCH_FALLBACK", "status": "SUCCEEDED"},
        search_attempt={"status": "SUCCEEDED"},
        model_calls=calls,
    )

    validate_production_collection_contract(direct)
    validate_production_collection_contract(fallback)

    direct.search_attempt = {"status": "SUCCEEDED"}
    with pytest.raises(ValueError, match="must not perform"):
        validate_production_collection_contract(direct)


def test_image_tables_are_transcribed_into_full_source_text_and_audited(monkeypatch, tmp_path) -> None:
    import metric_pulse.collector as collector_module
    import metric_pulse.source_pipeline as pipeline

    collector_module._IMAGE_TABLE_CACHE.clear()
    pipeline._SOURCE_CACHE.clear()
    monkeypatch.setattr(get_settings(), "source_cache_root", tmp_path)
    responses = [
        {
            "has_data_table": False,
            "description": "Decorative technology photo",
            "columns": [],
            "rows": [],
            "confidence": 0.99,
        },
        {
            "has_data_table": True,
            "description": "2021 AI innovation scores",
            "columns": ["排名", "国家", "得分"],
            "rows": [
                [10, "澳大利亚", 26.81],
                [22, "爱尔兰", 16.68],
                [24, "爱沙尼亚", 15.14],
            ],
            "confidence": 0.98,
        },
    ]
    prompts: list[str] = []

    class FakeClient:
        def __init__(self) -> None:
            self.last_response_metadata = {"usage": {"total_tokens": 123}}

        async def generate_json(self, *, system, prompt, image_png=None):
            assert image_png is not None
            prompts.append(prompt)
            return responses.pop(0)

    document = SourceDocument(
        index=1,
        url="https://example.com/article",
        normalized_url="https://example.com/article",
        cache_key="https://example.com/article",
        title="2021全球人工智能创新指数报告",
        media_type="text/html",
        text=(
            "Article opening.\n[[METRIC_PULSE_IMAGE:1]]\n"
            "Figure: 2021 scores.\n[[METRIC_PULSE_IMAGE:2]]\nArticle ending."
        ),
        images=[
            ImageEvidence(
                "Source 1, decorative",
                b"decorative-image",
                1,
                description="decorative",
                marker="[[METRIC_PULSE_IMAGE:1]]",
            ),
            ImageEvidence(
                "Source 1, score chart",
                b"score-chart",
                1,
                description="2021年各国人工智能创新指数得分与排名",
                marker="[[METRIC_PULSE_IMAGE:2]]",
            ),
        ],
    )

    calls = asyncio.run(enrich_document_image_tables(document, FakeClient()))

    assert [item["phase"] for item in calls] == ["VISION_TABLE", "VISION_TABLE"]
    assert [item["status"] for item in calls] == ["SUCCEEDED", "SUCCEEDED"]
    assert "Decorative technology photo" not in document.text
    assert "| 排名 | 国家 | 得分 |" in document.text
    assert "| 10 | 澳大利亚 | 26.81 |" in document.text
    assert "| 22 | 爱尔兰 | 16.68 |" in document.text
    assert "| 24 | 爱沙尼亚 | 15.14 |" in document.text
    assert "Article opening." in document.text and "Article ending." in document.text
    assert "METRIC_PULSE_IMAGE" not in document.text
    rendered = render_source_documents([document], {"region": "爱尔兰"})
    assert "Article opening." in rendered and "Article ending." in rendered
    assert "| 22 | 爱尔兰 | 16.68 |" in rendered
    assert document.image_table_results[1]["has_data_table"] is True
    assert all("Source article title: 2021全球人工智能创新指数报告" in prompt for prompt in prompts)
    assert "2021年各国人工智能创新指数得分与排名" in prompts[1]


def test_image_table_normalization_salvages_irregular_long_table_rows() -> None:
    result = _normalize_image_table_response(
        {
            "has_data_table": True,
            "description": "国家排名",
            "columns": ["排名", "国家", "得分"],
            "guessed_columns": ["得分"],
            "rows": [[1, "美国", 59.43], [2, "中国"], [3, "英国", 45.07, "备注"]],
            "confidence": 0.96,
        }
    )

    assert result["shape_adjusted"] is True
    assert result["columns"] == ["排名", "国家", "得分[推测]", "未命名列1[推测]"]
    assert result["guessed_columns"] == ["得分[推测]", "未命名列1[推测]"]
    assert result["rows"] == [
        [1, "美国", 59.43, None],
        [2, "中国", None, None],
        [3, "英国", 45.07, "备注"],
    ]


def test_image_table_truncation_retries_with_larger_output_budget(monkeypatch, tmp_path) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "source_cache_root", tmp_path)
    monkeypatch.setattr(settings, "vision_table_max_output_tokens", 8_192)
    monkeypatch.setattr(settings, "vision_table_retry_max_output_tokens", 16_384)

    class RetryClient(OMLXClient):
        def __init__(self) -> None:
            super().__init__(settings)
            self.budgets: list[int | None] = []

        async def generate_json(self, *, system, prompt, image_png=None, max_output_tokens=None):
            self.budgets.append(max_output_tokens)
            if len(self.budgets) == 1:
                self.last_response_metadata = {"finish_reason": "length"}
                raise OMLXError("Unterminated string in JSON response")
            self.last_response_metadata = {"finish_reason": "stop"}
            return {
                "has_data_table": True,
                "description": "ranking",
                "columns": ["country", "score"],
                "rows": [["Ireland", 16.68]],
                "confidence": 1,
            }

    document = SourceDocument(
        index=1,
        url="https://example.com/long-table",
        text="[[METRIC_PULSE_IMAGE:1]]",
        images=[
            ImageEvidence(
                "long table",
                b"unique-long-table-image",
                1,
                marker="[[METRIC_PULSE_IMAGE:1]]",
            )
        ],
    )
    client = RetryClient()

    calls = asyncio.run(enrich_document_image_tables(document, client))

    assert client.budgets == [8_192, 16_384]
    assert [call["status"] for call in calls] == ["FAILED", "SUCCEEDED"]
    assert calls[0]["output_summary"]["will_retry"] is True
    assert "| Ireland | 16.68 |" in document.text


def test_production_contract_allows_only_vision_calls_before_synthesize_and_verify() -> None:
    result = CollectionResult(
        values={"data": 1},
        acquisition_attempt={"route": "DIRECT_LINK", "status": "SUCCEEDED"},
        model_calls=[
            {"phase": "VISION_TABLE", "model": "Qwen3.8-27B-6bit"},
            {"phase": "SYNTHESIZE", "model": "Qwen3.8-27B-6bit"},
            {"phase": "VERIFY", "model": "Qwen3.8-27B-6bit"},
        ],
    )

    validate_production_collection_contract(result)
    result.model_calls[0]["phase"] = "VERIFY"
    with pytest.raises(ValueError, match="VISION_TABLE"):
        validate_production_collection_contract(result)


def test_same_direct_source_fetches_once_but_keeps_two_model_calls_per_row(monkeypatch, tmp_path) -> None:
    import metric_pulse.source_pipeline as pipeline

    pipeline._SOURCE_CACHE.clear()
    pipeline._SOURCE_CACHE_LOCKS.clear()
    monkeypatch.setattr(pipeline.get_settings(), "source_cache_root", tmp_path)
    monkeypatch.setattr(get_settings(), "browser_fallback_enabled", False)
    fetches = 0
    prompts: list[str] = []

    async def fake_fetch(candidate, index, client, validate_url):
        nonlocal fetches
        fetches += 1
        return SourceDocument(
            index=index,
            url=candidate.source_url,
            requested_url=candidate.source_url,
            normalized_url=candidate.source_url,
            media_type="text/csv",
            text="developers,iso2_code,year,quarter\n125033,EC,2021,3",
            content_hash="c" * 64,
        )

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            prompts.append(prompt)
            if len(prompts) % 2:
                return {
                    "values": {"data": 125033},
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                }
            return {
                "approved": True,
                "values": {"data": 125033},
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
                "reason": "unique row",
            }

    async def search_must_not_run(_query, *, limit):
        raise AssertionError("direct unique match must not search")

    monkeypatch.setattr(pipeline, "fetch_source_document", fake_fetch)
    monkeypatch.setattr("metric_pulse.collector.discover_sources", search_must_not_run)
    collector = OMLXCollector(FakeClient())

    for entity in ("ROW_A", "ROW_B"):
        record = DataRecord(
            sheet_name="Sheet1",
            source_row=2,
            business_key=entity,
            raw_data={"source_url": "https://example.com/developers.csv"},
            row_contract={
                "descriptors": {
                    "region": "EC",
                    "statistical_date": "2021Q3",
                    "entity_marker": entity,
                }
            },
        )
        result = asyncio.run(collector.collect(record, CollectionUnit(target_fields=["data"])))
        assert result.acquisition_attempt["route"] == "DIRECT_LINK"

    assert fetches == 1
    assert len(prompts) == 4
    assert all("ROW_B" not in prompt for prompt in prompts[:2])
    assert all("ROW_A" not in prompt for prompt in prompts[2:])
    shared_prefixes = [
        prompt.split("<shared_sources>\n", 1)[1].split("\n</shared_sources>", 1)[0]
        for prompt in prompts
    ]
    # SYNTHESIZE 与 VERIFY 的系统提示不同，只比较相同阶段在相邻行之间的可缓存前缀。
    assert shared_prefixes[0] == shared_prefixes[2]
    assert shared_prefixes[1] == shared_prefixes[3]
    assert all(prompt.index("numbered_sources") < prompt.index("row_contract") for prompt in prompts)


def test_github_monthly_rank_uses_fixed_source_and_isolates_one_repository(monkeypatch) -> None:
    prompts: list[str] = []
    snapshot_at = "2026-08-24T21:00:00+08:00"
    headers = list(AI_ALGORITHM_COLLECTION_TARGET_FIELDS)
    raw_data, row_contract, target_fields = ai_algorithm_collection_row_contract(
        sheet_name="人工智能算法收藏(ai_algorithm_collectio",
        source_row=7,
        rank=4,
        snapshot_at=snapshot_at,
        headers=headers,
    )
    repositories = [
        {"full_name": f"owner/repository-{rank}", "stargazers_count": 200_000 - rank * 1_111}
        for rank in range(1, 13)
    ]

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            prompts.append(prompt)
            if len(prompts) == 1:
                return {
                    "values": {"name": "owner/repository-4", "star": 195},
                    "source_indices": [1],
                }
            return {
                "approved": True,
                # 即使模型值偏离，应用也只能采用程序从同一证据生成的确定性结果。
                "values": {"name": "model/wrong", "star": 999_999},
                "source_indices": [1],
                "constraint_matches": {"rank": True},
                "confidence": 1,
                "conflicts": [],
                "reason": "the one-row GitHub evidence matches rank four",
            }

    async def fake_gather(candidates, *_args, **_kwargs):
        assert len(candidates) == 1
        assert candidates[0].source_url == GITHUB_TOP_REPOSITORIES_API_URL
        return [
            SourceDocument(
                index=1,
                url=GITHUB_TOP_REPOSITORIES_API_URL,
                requested_url=GITHUB_TOP_REPOSITORIES_API_URL,
                normalized_url=GITHUB_TOP_REPOSITORIES_API_URL,
                media_type="application/json",
                text=json.dumps({"items": repositories}),
                content_hash="d" * 64,
            )
        ]

    async def search_must_not_run(_query, *, limit):
        raise AssertionError("the fixed GitHub ranking profile must not use generic search")

    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    monkeypatch.setattr("metric_pulse.collector.discover_sources", search_must_not_run)

    result = asyncio.run(
        OMLXCollector(FakeClient()).collect(
            DataRecord(
                sheet_name="人工智能算法收藏(ai_algorithm_collectio",
                source_row=7,
                business_key="snapshot-rank-4",
                raw_data=raw_data,
                row_contract=row_contract,
            ),
            CollectionUnit(target_fields=target_fields),
        )
    )

    expected_star = repositories[3]["stargazers_count"] // 1_000
    assert result.values["name"] == "owner/repository-4"
    assert result.values["star"] == expected_star
    assert result.values["star_unit"] == "k"
    assert result.values["rank"] == 4
    assert result.values["collect_date"] == snapshot_at
    assert result.values["datasource_date"] == snapshot_at
    assert result.values["collection_date"] == snapshot_at
    assert result.values["source_department"] == "Github"
    assert result.values["source_url"] == GITHUB_TOP_REPOSITORIES_SOURCE_URL
    assert result.values["update_frequency"] == "month"
    assert result.values["data_type"] == "采集"
    assert result.values["data_status"] == "新增"
    assert len(result.values["logic_id"]) == 64
    assert result.search_attempt is None
    assert result.acquisition_attempt["route"] == "DIRECT_LINK"
    assert len(prompts) == 2
    assert all("owner/repository-4" in prompt for prompt in prompts)
    assert all("owner/repository-3" not in prompt for prompt in prompts)
    assert all("owner/repository-5" not in prompt for prompt in prompts)
    assert (
        result.validation["deterministic_profile_values"]["exact_stargazers_count"]
        == (repositories[3]["stargazers_count"])
    )


def test_github_monthly_rank_fails_closed_without_search_when_snapshot_is_short(
    monkeypatch,
) -> None:
    async def fake_gather(*_args, **_kwargs):
        return [
            SourceDocument(
                index=1,
                url=GITHUB_TOP_REPOSITORIES_API_URL,
                media_type="application/json",
                text=json.dumps(
                    {
                        "items": [
                            {"full_name": f"owner/repository-{rank}", "stargazers_count": 20_000}
                            for rank in range(1, 10)
                        ]
                    }
                ),
            )
        ]

    async def search_must_not_run(_query, *, limit):
        raise AssertionError("an incomplete fixed-source snapshot must not fall back to search")

    class ModelMustNotRun:
        async def generate_json(self, **_kwargs):
            raise AssertionError("the model must not run on an incomplete deterministic snapshot")

    raw_data, row_contract, target_fields = ai_algorithm_collection_row_contract(
        sheet_name="人工智能算法收藏(ai_algorithm_collectio",
        source_row=4,
        rank=1,
        snapshot_at="2026-08-24T21:00:00+08:00",
        headers=list(AI_ALGORITHM_COLLECTION_TARGET_FIELDS),
    )
    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    monkeypatch.setattr("metric_pulse.collector.discover_sources", search_must_not_run)

    with pytest.raises(RuntimeError, match="GitHub ranking acquisition is incomplete"):
        asyncio.run(
            OMLXCollector(ModelMustNotRun()).collect(
                DataRecord(
                    sheet_name="人工智能算法收藏(ai_algorithm_collectio",
                    source_row=4,
                    business_key="snapshot-rank-1",
                    raw_data=raw_data,
                    row_contract=row_contract,
                ),
                CollectionUnit(target_fields=target_fields),
            )
        )
