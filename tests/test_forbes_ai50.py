from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from metric_pulse.collector import OMLXCollector, apply_top_list_ai_values
from metric_pulse.dataset_profiles import (
    FORBES_AI50_SOURCE_URL,
    TOP_LIST_AI_PROFILE,
    top_list_ai_row_contract,
)
from metric_pulse.forbes_ai50 import (
    ForbesAI50Error,
    compact_forbes_ai50_html,
    funding_millions_to_yi,
    parse_forbes_ai50_snapshot,
    prepare_forbes_ai50_company_document,
)
from metric_pulse.models import CollectionUnit, DataRecord
from metric_pulse.source_pipeline import SourceDocument

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
    "nextcycle_time",
    "updater",
    "editor",
    "checker",
    "edit_time",
    "data_type",
    "data_status",
]


def forbes_snapshot_payload(*, count: int = 50, year: int = 2026) -> str:
    return json.dumps(
        {
            "profile": "forbes_ai50_official_snapshot_v1",
            "year": str(year),
            "total_count": 50,
            "published_at": "4/16/2026 6:30 am",
            "title": "Forbes 2026 AI 50 List",
            "source_url": FORBES_AI50_SOURCE_URL,
            "funding_format": {"currency": "USD", "magnitude": "Millions"},
            "companies": [
                {
                    "list_position": position,
                    "rank": None,
                    "company_name": f"Company {position:02d}",
                    "CEO": f"CEO {position}",
                    "city": "San Francisco",
                    "state": "California",
                    "country": "United States",
                    "funding_millions": "2100" if position == 7 else str(100 + position),
                    "establish_date": 2010 + position % 10,
                    "description": f"Official description for Company {position:02d}.",
                    "profile_uri": f"company-{position:02d}",
                }
                for position in range(1, count + 1)
            ],
        },
        ensure_ascii=False,
    )


@pytest.mark.parametrize(
    ("millions", "expected_yi"),
    [("60000", "600"), ("392", "3.92"), ("2100", "21")],
)
def test_funding_millions_to_yi_is_deterministic(millions: str, expected_yi: str) -> None:
    assert funding_millions_to_yi(millions) == Decimal(expected_yi)


def test_parse_forbes_snapshot_requires_complete_unranked_current_year() -> None:
    snapshot = parse_forbes_ai50_snapshot(forbes_snapshot_payload(), expected_year=2026)

    assert snapshot.year == 2026
    assert len(snapshot.companies) == 50
    assert snapshot.published_at == "2026-04-16T06:30:00-04:00"
    assert snapshot.companies[6].funding_raw == "$2.1 BIL"
    assert snapshot.companies[6].financing_amount_yi == 21


def test_compact_forbes_next_data_keeps_all_fifty_business_rows() -> None:
    compact = json.loads(forbes_snapshot_payload())
    rows = [
        {
            "position": item["list_position"],
            "rank": item["rank"],
            "organizationName": item["company_name"],
            "ceoName": item["CEO"],
            "city": item["city"],
            "state": item["state"],
            "country": item["country"],
            "funding": item["funding_millions"],
            "yearFounded": item["establish_date"],
            "description": item["description"],
            "uri": item["profile_uri"],
        }
        for item in compact["companies"]
    ]
    next_data = {
        "props": {
            "pageProps": {
                "meta": {
                    "datetime": compact["published_at"],
                    "title": compact["title"],
                },
                "schema": {
                    "blocks": [
                        {
                            "component": "Table",
                            "data": {
                                "listUri": "ai50",
                                "year": compact["year"],
                                "totalCount": 50,
                                "dataRows": rows,
                            },
                        }
                    ]
                },
            }
        }
    }
    html = (
        "<html><body><script id=\"__NEXT_DATA__\" type=\"application/json\">"
        + json.dumps(next_data)
        + "</script></body></html>"
    )

    result = compact_forbes_ai50_html(html)
    snapshot = parse_forbes_ai50_snapshot(result or "", expected_year=2026)

    assert len(snapshot.companies) == 50
    assert snapshot.companies[0].company_name == "Company 01"
    assert snapshot.companies[-1].company_name == "Company 50"


@pytest.mark.parametrize(
    "mutation",
    ["short", "duplicate", "ranked", "wrong_year", "invalid_funding"],
)
def test_parse_forbes_snapshot_fails_closed(mutation: str) -> None:
    payload = json.loads(forbes_snapshot_payload())
    if mutation == "short":
        payload["companies"].pop()
    elif mutation == "duplicate":
        payload["companies"][1]["company_name"] = payload["companies"][0]["company_name"]
    elif mutation == "ranked":
        payload["companies"][0]["rank"] = 1
    elif mutation == "wrong_year":
        payload["year"] = "2025"
    else:
        payload["companies"][0]["funding_millions"] = "not-a-number"

    with pytest.raises(ForbesAI50Error):
        parse_forbes_ai50_snapshot(json.dumps(payload), expected_year=2026)


def test_prepare_forbes_company_document_exposes_only_one_company() -> None:
    document = SourceDocument(
        index=1,
        url=FORBES_AI50_SOURCE_URL,
        requested_url=FORBES_AI50_SOURCE_URL,
        normalized_url=FORBES_AI50_SOURCE_URL,
        text=forbes_snapshot_payload(),
        media_type="application/json",
        content_hash="full-official-snapshot-hash",
    )

    sliced, values = prepare_forbes_ai50_company_document(
        document,
        list_position=7,
        expected_year=2026,
    )

    assert sliced.url == FORBES_AI50_SOURCE_URL
    assert sliced.content_hash == "full-official-snapshot-hash"
    assert len(sliced.text.splitlines()) == 2
    assert "Company 07" in sliced.text
    assert "Company 06" not in sliced.text
    assert values["financing_amount"] == 21
    assert values["funding_formula"] == "2100 百万美元 x 0.01 = 21 亿美元"


def test_missing_structured_ceo_can_keep_explicitly_verified_official_description_value() -> None:
    result = apply_top_list_ai_values(
        values={
            "logic_id": None,
            "rank_year": None,
            "company_name": "model name",
            "headquarter_location": "美国加利福尼亚州旧金山",
            "CEO": "Officially named CEO",
            "financing_amount": 999,
            "financing_amount_unit": None,
            "establish_date": 1900,
            "source": None,
            "source_url": None,
            "update_frequency": None,
            "datasource_date": None,
            "collection_date": None,
            "data_type": None,
            "data_status": None,
        },
        row_contract={
            "profile": TOP_LIST_AI_PROFILE,
            "canonical_source_url": FORBES_AI50_SOURCE_URL,
            "fixed_values": {
                "rank_year": 2026,
                "source": "福布斯",
                "update_frequency": "year",
                "collection_date": "2026-08-24T22:00:00+08:00",
                "data_type": "采集",
                "data_status": "新增",
            },
        },
        deterministic_values={
            "rank_year": 2026,
            "company_name": "Official Company",
            "CEO": None,
            "financing_amount": 3.92,
            "establish_date": 2022,
            "datasource_date": "2026-04-16T06:30:00-04:00",
        },
        evidence_approved=True,
    )

    assert result["company_name"] == "Official Company"
    assert result["CEO"] == "Officially named CEO"
    assert result["financing_amount"] == 3.92
    assert result["financing_amount_unit"] == "亿美元"


def test_forbes_collector_uses_one_official_slice_two_models_and_no_search(monkeypatch) -> None:
    calls: list[str] = []
    gather_calls = 0

    class FakeClient:
        async def generate_json(self, *, system, prompt, image_png=None):
            calls.append(prompt)
            values = {
                "company_name": "model invented name",
                "headquarter_location": "美国加利福尼亚州旧金山",
                "CEO": "model invented CEO",
                "financing_amount": 999,
                "establish_date": 1900,
            }
            if len(calls) == 1:
                return {
                    "values": values,
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                }
            return {
                "approved": True,
                "values": values,
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
                "constraint_matches": {"list_position": True, "rank_year": True},
                "reason": "one official company row",
            }

    async def fake_gather(candidates, *_args, **_kwargs):
        nonlocal gather_calls
        gather_calls += 1
        assert len(candidates) == 1
        assert candidates[0].source_url == FORBES_AI50_SOURCE_URL
        assert candidates[0].metadata["cache_scope"] == "2026-08-24T22:00:00+08:00"
        return [
            SourceDocument(
                index=1,
                url=FORBES_AI50_SOURCE_URL,
                requested_url=FORBES_AI50_SOURCE_URL,
                normalized_url=FORBES_AI50_SOURCE_URL,
                text=forbes_snapshot_payload(),
                media_type="application/json",
                content_hash="full-official-snapshot-hash",
            )
        ]

    async def forbidden_search(*_args, **_kwargs):
        raise AssertionError("Forbes fixed profile must not call search")

    monkeypatch.setattr("metric_pulse.collector.gather_source_documents", fake_gather)
    monkeypatch.setattr("metric_pulse.collector.discover_sources", forbidden_search)
    raw, contract, targets = top_list_ai_row_contract(
        sheet_name="TOP50企业排名(top_list_ai)",
        source_row=209,
        list_position=7,
        snapshot_at="2026-08-24T22:00:00+08:00",
        rank_year=2026,
        headers=TOP_LIST_HEADERS,
        superseded_rows=list(range(153, 203)),
    )
    record = DataRecord(
        sheet_name=contract["sheet_name"],
        source_row=209,
        business_key="forbes-position-7",
        raw_data=raw,
        row_contract=contract,
    )
    unit = CollectionUnit(target_fields=targets)

    result = asyncio.run(OMLXCollector(FakeClient()).collect(record, unit))

    assert gather_calls == 1
    assert len(calls) == 2
    assert [item["phase"] for item in result.model_calls] == ["SYNTHESIZE", "VERIFY"]
    assert result.search_attempt is None
    assert result.acquisition_attempt["route"] == "DIRECT_LINK"
    assert result.acquisition_attempt["match_status"] == "OFFICIAL_ANNUAL_POSITION_MATCH"
    assert "Company 07" in calls[0]
    assert "Company 06" not in calls[0]
    assert result.values["company_name"] == "Company 07"
    assert result.values["headquarter_location"] == "美国加利福尼亚州旧金山"
    assert result.values["CEO"] == "CEO 7"
    assert result.values["financing_amount"] == 21
    assert result.values["financing_amount_unit"] == "亿美元"
    assert result.values["source_url"] == FORBES_AI50_SOURCE_URL
    assert result.values["rank_year"] == 2026
    assert result.values["data_status"] == "新增"
    assert result.validation["dataset_profile"] == TOP_LIST_AI_PROFILE
    assert result.validation["deterministic_profile_values"]["list_position_is_rank"] is False
