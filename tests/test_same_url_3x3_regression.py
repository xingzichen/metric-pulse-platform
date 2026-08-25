from __future__ import annotations

import asyncio
import io
import json

from PIL import Image

from metric_pulse.collector import OMLXCollector
from metric_pulse.config import get_settings
from metric_pulse.models import CollectionUnit, DataRecord
from metric_pulse.source_pipeline import ImageEvidence, SourceDocument

BAIJIAHAO_URL = "https://baijiahao.baidu.com/s?id=1742955719106424432"
WORLD_BANK_URL = "https://data.worldbank.org.cn/indicator/GB.XPD.RSDV.GD.ZS?locations=US"


def test_baijiahao_and_world_bank_same_url_3x3_regression(monkeypatch, tmp_path) -> None:
    """冻结 3+3 来源证据，回归同 URL 抓取/识图复用与逐行模型隔离。"""

    import metric_pulse.collector as collector_module
    import metric_pulse.source_pipeline as pipeline

    pipeline._SOURCE_CACHE.clear()
    pipeline._SOURCE_CACHE_LOCKS.clear()
    collector_module._IMAGE_TABLE_CACHE.clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "source_cache_root", tmp_path)
    monkeypatch.setattr(settings, "source_host_min_interval_seconds", 0)
    monkeypatch.setattr(settings, "browser_fallback_enabled", False)
    monkeypatch.setattr(settings, "vision_analysis_enabled", True)
    monkeypatch.setattr(settings, "vision_table_enrichment_enabled", True)
    fetches: dict[str, int] = {}
    image_buffer = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(image_buffer, format="PNG")
    frozen_image = image_buffer.getvalue()

    async def fake_fetch(candidate, index, client, validate_url):
        fetches[candidate.source_url] = fetches.get(candidate.source_url, 0) + 1
        if candidate.source_url.startswith("https://baijiahao.baidu.com/"):
            return SourceDocument(
                index=index,
                url=candidate.source_url,
                title="2021全球人工智能创新指数报告",
                media_type="text/html",
                text="报告正文\n[[METRIC_PULSE_IMAGE:1]]\n图片说明: 各国创新指数排名与得分",
                images=[
                    ImageEvidence(
                        "Source 1 score table",
                        frozen_image,
                        index,
                        description="2021年各国人工智能创新指数排名与得分",
                        marker="[[METRIC_PULSE_IMAGE:1]]",
                    )
                ],
            )
        assert candidate.source_url.startswith("https://api.worldbank.org/")
        return SourceDocument(
            index=index,
            url=candidate.source_url,
            title="World Bank R&D expenditure",
            media_type="text/csv",
            text=(
                "country,country_id,date,value,decimal\n"
                "United States,US,2018,2.98956,2\n"
                "United States,US,2019,3.14297,2\n"
                "United States,US,2020,3.41788,2"
            ),
        )

    class FakeClient:
        def __init__(self) -> None:
            self.prompts: list[str] = []
            self.vision_calls = 0

        async def generate_json(self, *, system, prompt, image_png=None):
            self.prompts.append(prompt)
            if image_png is not None and "Inspect this single source image" in prompt:
                self.vision_calls += 1
                return {
                    "has_data_table": True,
                    "description": "2021年各国人工智能创新指数",
                    "columns": ["排名", "国家", "得分"],
                    "guessed_columns": [],
                    "rows": [
                        [10, "澳大利亚", 26.81],
                        [22, "爱尔兰", 16.68],
                        [24, "爱沙尼亚", 15.14],
                    ],
                    "confidence": 1,
                }
            row_payload = prompt.split("<row_request>\n", 1)[-1].split(
                "\n</row_request>", 1
            )[0]
            if "<audit_request>" in prompt:
                row_payload = prompt.split("<audit_request>\n", 1)[1].split(
                    "\n</audit_request>", 1
                )[0]
            request = json.loads(row_payload)
            descriptors = request["row_contract"]["descriptors"]
            if "country" in descriptors:
                value = {
                    "澳大利亚": 26.81,
                    "爱尔兰": 16.68,
                    "爱沙尼亚": 15.14,
                }[descriptors["country"]]
            else:
                value = {
                    "2018": 2.98956,
                    "2019": 3.14297,
                    "2020": 3.41788,
                }[str(descriptors["statistical_date"])]
            if "<audit_request>" in prompt:
                return {
                    "approved": True,
                    "values": {"be_data": value},
                    "source_indices": [1],
                    "confidence": 1,
                    "conflicts": [],
                    "constraint_matches": {},
                    "reason": "frozen exact row",
                }
            return {
                "values": {"be_data": value},
                "source_indices": [1],
                "confidence": 1,
                "conflicts": [],
                "constraint_matches": {},
            }

    async def search_must_not_run(_query, *, limit):
        raise AssertionError("all six rows must use their direct source")

    monkeypatch.setattr(pipeline, "fetch_source_document", fake_fetch)
    monkeypatch.setattr(collector_module, "discover_sources", search_must_not_run)
    model = FakeClient()
    collector = OMLXCollector(model)

    cases = [
        *[(BAIJIAHAO_URL, {"country": country}, expected) for country, expected in (
            ("澳大利亚", 26.81),
            ("爱尔兰", 16.68),
            ("爱沙尼亚", 15.14),
        )],
        *[
            (WORLD_BANK_URL, {"region": "United States", "statistical_date": year}, expected)
            for year, expected in (
                ("2018", 2.98956),
                ("2019", 3.14297),
                ("2020", 3.41788),
            )
        ],
    ]
    results = []
    for position, (url, descriptors, expected) in enumerate(cases, start=1):
        record = DataRecord(
            sheet_name="ai_index",
            source_row=6_890 + position,
            business_key=f"ai-index-regression-{position}",
            raw_data={"source_url": url},
            row_contract={"descriptors": descriptors},
        )
        result = asyncio.run(collector.collect(record, CollectionUnit(target_fields=["be_data"])))
        assert result.values["be_data"] == expected
        assert result.acquisition_attempt["route"] == "DIRECT_LINK"
        results.append(result)

    assert sum(fetches.values()) == 2
    assert len(fetches) == 2
    assert model.vision_calls == 1
    assert [len(result.model_calls) for result in results] == [3, 2, 2, 2, 2, 2]
    assert all(
        [call["phase"] for call in result.model_calls[-2:]] == ["SYNTHESIZE", "VERIFY"]
        for result in results
    )
