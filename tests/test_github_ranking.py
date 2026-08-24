from __future__ import annotations

import json

import pytest

from metric_pulse.dataset_profiles import GITHUB_TOP_REPOSITORIES_SOURCE_URL
from metric_pulse.github_ranking import (
    GitHubRankingError,
    parse_github_top_repositories,
    prepare_github_rank_document,
)
from metric_pulse.source_pipeline import SourceDocument


def github_payload(counts: list[int]) -> str:
    return json.dumps(
        {
            "items": [
                {"full_name": f"owner/repo-{index}", "stargazers_count": count}
                for index, count in enumerate(counts, start=1)
            ]
        }
    )


def test_parse_github_top_repositories_sorts_top_ten_and_uses_integer_k() -> None:
    counts = [10_001, 50_999, 50_999, 22_100, 99_999, 70_000, 65_123, 30_000, 40_000, 20_000, 15_000, 12_000]

    ranked = parse_github_top_repositories(github_payload(counts))

    assert len(ranked) == 10
    assert [item.exact_stargazers_count for item in ranked] == sorted(counts, reverse=True)[:10]
    # 相同收藏量保留 API 中的原始先后顺序。
    tied = [item.name for item in ranked if item.exact_stargazers_count == 50_999]
    assert tied == ["owner/repo-2", "owner/repo-3"]
    assert ranked[0].star == ranked[0].exact_stargazers_count // 1_000
    assert all(item.star_unit == "k" for item in ranked)


@pytest.mark.parametrize(
    "text",
    [
        "not-json",
        json.dumps({"items": []}),
        json.dumps({"items": [{}] * 10}),
        json.dumps(
            {
                "incomplete_results": True,
                "items": [
                    {"full_name": f"owner/repo-{index}", "stargazers_count": 20_000 + index}
                    for index in range(10)
                ],
            }
        ),
    ],
)
def test_parse_github_top_repositories_fails_closed_for_incomplete_snapshot(text: str) -> None:
    with pytest.raises(GitHubRankingError):
        parse_github_top_repositories(text)


def test_prepare_github_rank_document_exposes_only_one_repository_to_model() -> None:
    api_url = "https://api.github.com/search/repositories?q=example"
    document = SourceDocument(
        index=1,
        url=api_url,
        requested_url=api_url,
        normalized_url=api_url,
        text=github_payload([100_000 - index * 1_001 for index in range(12)]),
        media_type="application/json",
        content_hash="original-full-snapshot-hash",
    )

    sliced, values = prepare_github_rank_document(document, rank=4)

    assert sliced.url == GITHUB_TOP_REPOSITORIES_SOURCE_URL
    assert sliced.requested_url == api_url
    assert sliced.normalized_url == api_url
    assert sliced.content_hash == "original-full-snapshot-hash"
    assert sliced.text.splitlines()[0] == "rank,name,star,star_unit,exact_stargazers_count"
    assert len(sliced.text.splitlines()) == 2
    assert values["name"] in sliced.text
    assert "owner/repo-1," not in sliced.text
