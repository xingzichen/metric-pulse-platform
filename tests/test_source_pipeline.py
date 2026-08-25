from __future__ import annotations

import io
from types import SimpleNamespace

import pymupdf
from docx import Document
from PIL import Image

from metric_pulse.source_pipeline import (
    ImageEvidence,
    ImageReference,
    SourceDocument,
    browser_fallback_reason,
    build_contact_sheet,
    compact_json_records,
    extract_docx,
    extract_html_main_content,
    extract_pdf,
    fetch_source_document,
    gather_source_documents,
    looks_like_challenge_page,
    normalize_source_url,
)


def test_html_extraction_keeps_main_content_and_drops_boilerplate() -> None:
    text, images = extract_html_main_content(
        b"""
        <html><body>
          <nav>unrelated navigation</nav>
          <main><h1>AI report</h1><p>Sweden created 42 AI companies in 2025.</p>
          <img src="/chart.png" alt="AI companies chart" width="800" height="500">
          <img src="/logo.png" alt="logo" width="100" height="50"></main>
          <footer>unrelated footer</footer>
        </body></html>
        """,
        "https://example.com/report",
    )

    assert "42 AI companies" in text
    assert "unrelated navigation" not in text
    assert "unrelated footer" not in text
    assert "[[METRIC_PULSE_IMAGE:1]]" in text
    assert images == [
        ImageReference(
            url="https://example.com/chart.png",
            description=("alt: AI companies chart | 邻近正文: Sweden created 42 AI companies in 2025."),
            marker="[[METRIC_PULSE_IMAGE:1]]",
        )
    ]


def test_world_bank_page_is_normalized_to_official_structured_api() -> None:
    assert normalize_source_url("https://data.worldbank.org.cn/indicator/GB.XPD.RSDV.GD.ZS?locations=US") == (
        "https://api.worldbank.org/v2/zh/country/US/indicator/GB.XPD.RSDV.GD.ZS?format=json&per_page=20000"
    )


def test_nested_json_record_array_is_compacted_to_matchable_csv() -> None:
    text = compact_json_records(
        b'[{"page":1},[{"country":{"id":"US","value":"United States"},'
        b'"date":"2019","value":3.14297,"decimal":2}]]'
    )

    assert text is not None
    assert text.splitlines()[0] == "country,country_id,date,value,decimal"
    assert "United States,US,2019,3.14297,2" in text


def test_github_api_keeps_raw_json_for_dedicated_ranking_parser(monkeypatch) -> None:
    import asyncio

    import metric_pulse.source_pipeline as pipeline

    payload = b'{"total_count":1,"incomplete_results":false,"items":[{"full_name":"a/b"}]}'

    async def fake_download(_client, url, _validate_url, **_kwargs):
        return url, "application/json", payload

    async def allow(_url):
        return None

    monkeypatch.setattr(pipeline, "_download", fake_download)
    candidate = SimpleNamespace(
        source_url="https://api.github.com/search/repositories?q=stars",
        title="Ranking",
        excerpt=None,
    )

    document = asyncio.run(fetch_source_document(candidate, 1, object(), allow))

    assert document.text.startswith('{"total_count":1')
    assert '"items"' in document.text


def test_pdf_extraction_includes_text_and_rendered_page() -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "AI investment reached 123 million in 2025")
    data = document.tobytes()
    document.close()

    text, images = extract_pdf(data, source_index=2)

    assert "123 million" in text
    assert images
    assert images[0].source_index == 2
    assert images[0].png.startswith(b"\x89PNG")


def test_docx_extraction_includes_paragraphs_and_tables() -> None:
    document = Document()
    document.add_paragraph("AI talent report")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Country"
    table.cell(0, 1).text = "People"
    table.cell(1, 0).text = "Sweden"
    table.cell(1, 1).text = "456"
    buffer = io.BytesIO()
    document.save(buffer)

    text, _ = extract_docx(buffer.getvalue(), source_index=3)

    assert "AI talent report" in text
    assert "Sweden\t456" in text


def test_contact_sheet_combines_visual_evidence() -> None:
    image = Image.new("RGB", (320, 200), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    document = SourceDocument(
        index=1,
        url="https://example.com/report",
        images=[ImageEvidence("Source 1 chart", buffer.getvalue(), 1)],
    )

    sheet = build_contact_sheet([document])

    assert sheet is not None
    assert sheet.startswith(b"\x89PNG")


def test_browser_fallback_targets_blocked_or_javascript_thin_pages() -> None:
    blocked = SourceDocument(
        index=1,
        url="https://example.com/report",
        http_status=403,
        error="HTTP 403: Forbidden",
    )
    thin = SourceDocument(
        index=2,
        url="https://example.com/dashboard",
        media_type="text/html",
        text="Loading...",
    )
    binary = SourceDocument(
        index=3,
        url="https://example.com/report.pdf",
        http_status=403,
        error="HTTP 403: Forbidden",
    )
    github_api = SourceDocument(
        index=4,
        url="https://api.github.com/search/repositories?q=stars",
        http_status=429,
        error="HTTP 429: Too Many Requests",
    )

    assert browser_fallback_reason(blocked, min_content_chars=500) == "HTTP 403"
    assert "shorter than 500" in (browser_fallback_reason(thin, min_content_chars=500) or "")
    assert browser_fallback_reason(binary, min_content_chars=500) is None
    assert browser_fallback_reason(github_api, min_content_chars=500) is None


def test_challenge_detection_does_not_flag_long_normal_article() -> None:
    assert looks_like_challenge_page("安全验证\n请输入验证码")
    assert looks_like_challenge_page('<script src="/cf-chl-/challenge-platform.js"></script>')
    long_article = "This research article discusses captcha systems. " + ("evidence " * 1_000)
    assert not looks_like_challenge_page(long_article)


def test_github_blob_url_is_normalized_to_raw_and_tracking_is_removed() -> None:
    normalized = normalize_source_url(
        "https://github.com/github/innovationgraph/blob/main/data/developers.csv?utm_source=test#readme"
    )

    assert normalized == ("https://raw.githubusercontent.com/github/innovationgraph/main/data/developers.csv")


def test_source_document_cache_survives_memory_cache_clear(monkeypatch, tmp_path) -> None:
    import metric_pulse.source_pipeline as pipeline

    pipeline._SOURCE_CACHE.clear()
    pipeline._SOURCE_CACHE_LOCKS.clear()
    monkeypatch.setattr(pipeline.get_settings(), "source_cache_root", tmp_path)
    fetches = 0

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
            content_hash="b" * 64,
        )

    async def allow(_url):
        return None

    monkeypatch.setattr(pipeline, "fetch_source_document", fake_fetch)
    candidate = SimpleNamespace(
        source_url="https://example.com/developers.csv",
        title="Developers",
        excerpt=None,
    )

    first = __import__("asyncio").run(
        gather_source_documents([candidate], allow, browser_fallback_enabled=False)
    )
    pipeline._SOURCE_CACHE.clear()
    second = __import__("asyncio").run(
        gather_source_documents([candidate], allow, browser_fallback_enabled=False)
    )

    assert fetches == 1
    assert first[0].persistent_cache_hit is False
    assert second[0].persistent_cache_hit is True
    assert second[0].text == first[0].text


def test_persistent_source_cache_restores_image_bytes_without_refetch(monkeypatch, tmp_path) -> None:
    import asyncio

    import metric_pulse.source_pipeline as pipeline

    pipeline._SOURCE_CACHE.clear()
    pipeline._SOURCE_CACHE_LOCKS.clear()
    settings = pipeline.get_settings()
    monkeypatch.setattr(settings, "source_cache_root", tmp_path)
    monkeypatch.setattr(settings, "source_host_min_interval_seconds", 0)
    fetches = 0
    image = Image.new("RGB", (24, 24), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    png = buffer.getvalue()

    async def fake_fetch(candidate, index, client, validate_url):
        nonlocal fetches
        fetches += 1
        return SourceDocument(
            index=index,
            url=candidate.source_url,
            media_type="text/html",
            text="Article body [[METRIC_PULSE_IMAGE:1]]",
            images=[
                ImageEvidence(
                    "Source 1 chart",
                    png,
                    index,
                    description="Figure 1: score table",
                    marker="[[METRIC_PULSE_IMAGE:1]]",
                )
            ],
        )

    async def allow(_url):
        return None

    monkeypatch.setattr(pipeline, "fetch_source_document", fake_fetch)
    candidate = SimpleNamespace(
        source_url="https://example.com/image-article",
        title="Image article",
        excerpt=None,
    )

    first = asyncio.run(gather_source_documents([candidate], allow, browser_fallback_enabled=False))
    pipeline._SOURCE_CACHE.clear()
    second = asyncio.run(gather_source_documents([candidate], allow, browser_fallback_enabled=False))

    assert fetches == 1
    assert first[0].images[0].png == png
    assert second[0].persistent_cache_hit is True
    assert second[0].images[0].png == png
    assert second[0].images[0].description == "Figure 1: score table"


def test_challenge_negative_cache_blocks_repeated_url_and_same_host(monkeypatch, tmp_path) -> None:
    import asyncio

    import metric_pulse.source_pipeline as pipeline

    pipeline._SOURCE_CACHE.clear()
    pipeline._SOURCE_CACHE_LOCKS.clear()
    settings = pipeline.get_settings()
    monkeypatch.setattr(settings, "source_cache_root", tmp_path)
    monkeypatch.setattr(settings, "source_host_min_interval_seconds", 0)
    monkeypatch.setattr(settings, "source_challenge_cooldown_seconds", 600)
    monkeypatch.setattr(settings, "source_cooldown_max_seconds", 600)
    fetches = 0

    async def fake_fetch(candidate, index, client, validate_url):
        nonlocal fetches
        fetches += 1
        return SourceDocument(
            index=index,
            url=candidate.source_url,
            http_status=403,
            error="HTTP 403: security challenge captcha",
        )

    async def allow(_url):
        return None

    monkeypatch.setattr(pipeline, "fetch_source_document", fake_fetch)

    def candidate(path: str):
        return SimpleNamespace(
            source_url=f"https://blocked.example.com/{path}",
            title="Blocked article",
            excerpt=None,
        )

    first = asyncio.run(
        gather_source_documents([candidate("one")], allow, browser_fallback_enabled=False)
    )
    repeated = asyncio.run(
        gather_source_documents([candidate("one")], allow, browser_fallback_enabled=False)
    )
    same_host = asyncio.run(
        gather_source_documents([candidate("two")], allow, browser_fallback_enabled=False)
    )

    assert fetches == 1
    assert first[0].source_failure_category == "CHALLENGE"
    assert repeated[0].error == "source cooldown active: CHALLENGE"
    assert same_host[0].error == "source cooldown active: CHALLENGE"
    assert repeated[0].retry_after_seconds > 0
    normalized = pipeline.normalize_source_url(candidate("one").source_url)
    pipeline._clear_source_failure(normalized, normalized)
    assert pipeline._active_source_cooldown(normalized, normalized) is None


def test_dynamic_snapshot_cache_is_reused_within_scope_but_refetched_for_new_scope(
    monkeypatch,
    tmp_path,
) -> None:
    import asyncio

    import metric_pulse.source_pipeline as pipeline

    pipeline._SOURCE_CACHE.clear()
    pipeline._SOURCE_CACHE_LOCKS.clear()
    monkeypatch.setattr(pipeline.get_settings(), "source_cache_root", tmp_path)
    fetches = 0

    async def fake_fetch(candidate, index, client, validate_url):
        nonlocal fetches
        fetches += 1
        return SourceDocument(
            index=index,
            url=candidate.source_url,
            requested_url=candidate.source_url,
            normalized_url=candidate.source_url,
            media_type="application/json",
            text=f'{{"fetch": {fetches}}}',
        )

    async def allow(_url):
        return None

    monkeypatch.setattr(pipeline, "fetch_source_document", fake_fetch)

    def candidate(scope: str):
        return SimpleNamespace(
            source_url="https://api.github.com/search/repositories?q=stars",
            title="Ranking",
            excerpt=None,
            metadata={"cache_scope": scope},
        )

    first = asyncio.run(gather_source_documents([candidate("snapshot-a")], allow))
    same_snapshot = asyncio.run(gather_source_documents([candidate("snapshot-a")], allow))
    new_snapshot = asyncio.run(gather_source_documents([candidate("snapshot-b")], allow))

    assert fetches == 2
    assert same_snapshot[0].cache_hit is True
    assert first[0].text == same_snapshot[0].text
    assert new_snapshot[0].text != first[0].text
    assert all(
        document.normalized_url == "https://api.github.com/search/repositories?q=stars"
        for document in (first[0], same_snapshot[0], new_snapshot[0])
    )
