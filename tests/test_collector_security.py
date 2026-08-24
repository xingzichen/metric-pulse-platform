from __future__ import annotations

import asyncio
import socket

import pytest

from metric_pulse.collector import (
    CollectionResult,
    EvidenceItem,
    OMLXCollector,
    apply_source_provenance,
    apply_verification,
    build_search_query,
    extract_structured_values,
    focus_evidence,
    render_search_evidence,
    structured_match_diagnostics,
    validate_public_url,
)
from metric_pulse.config import get_settings
from metric_pulse.models import CollectionUnit, DataRecord
from metric_pulse.processor import validate_production_collection_contract
from metric_pulse.source_pipeline import SourceDocument


def _address(ip: str, port: int = 443):
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (ip, port))


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
        ["be_data", "data"],
    )

    assert extracted == (
        {"be_data": 358848, "data": 358848},
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
    extracted = extract_structured_values(source, descriptors, ["be_data", "data"])

    assert diagnostics["status"] == "UNIQUE_MATCH"
    assert diagnostics["match_count"] == 1
    assert extracted == (
        {"be_data": 125033, "data": 125033},
        "developers,iso2_code,year,quarter\n125033,EC,2021,3",
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
                    "values": {"be_data": 125033, "data": 125033},
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                }
            return {
                "approved": True,
                "values": {"be_data": 125033, "data": 125033},
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
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
            "descriptors": {
                "region": "EC",
                "statistical_date": "2021Q3",
                "index_name": "Github开发者数量",
            }
        },
    )
    unit = CollectionUnit(target_fields=["be_data", "data"])

    result = asyncio.run(OMLXCollector(FakeClient()).collect(record, unit))

    assert result.values == {"be_data": 125033, "data": 125033}
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

    result = asyncio.run(
        OMLXCollector(FakeClient()).collect(record, CollectionUnit(target_fields=["data"]))
    )

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


def test_same_direct_source_fetches_once_but_keeps_two_model_calls_per_row(
    monkeypatch, tmp_path
) -> None:
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
        result = asyncio.run(
            collector.collect(record, CollectionUnit(target_fields=["data"]))
        )
        assert result.acquisition_attempt["route"] == "DIRECT_LINK"

    assert fetches == 1
    assert len(prompts) == 4
    assert all("ROW_B" not in prompt for prompt in prompts[:2])
    assert all("ROW_A" not in prompt for prompt in prompts[2:])
