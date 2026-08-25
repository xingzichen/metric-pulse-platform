from __future__ import annotations

from metric_pulse.task_service import source_affinity_key


def test_source_affinity_groups_equivalent_urls_but_not_url_less_rows() -> None:
    first = source_affinity_key(
        {"source_url": "https://example.com/report?utm_source=batch#table"},
        "row-a",
    )
    second = source_affinity_key({"source_url": "https://example.com/report"}, "row-b")

    assert first == second
    assert source_affinity_key({}, "row-a") != source_affinity_key({}, "row-b")
