"""GitHub 仓库收藏榜的确定性解析与逐名次证据切片。

GitHub 搜索 API 只负责取得与用户固定搜索页等价的结构化快照。应用稳定排序、选择前十、
把精确 star 数转换为整数 ``k``，再为每个名次生成只包含一行的证据，避免模型在同一会话
看到多个项目后串行或误配。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

from .dataset_profiles import (
    AI_ALGORITHM_COLLECTION_TOP_N,
    GITHUB_TOP_REPOSITORIES_SOURCE_URL,
)
from .source_pipeline import SourceDocument


class GitHubRankingError(ValueError):
    """GitHub 榜单响应不完整或不符合固定契约。"""


@dataclass(frozen=True, slots=True)
class GitHubRankedRepository:
    """一个经过程序验证的榜单名次。"""

    rank: int
    name: str
    star: int
    star_unit: str
    exact_stargazers_count: int


def parse_github_top_repositories(text: str) -> list[GitHubRankedRepository]:
    """解析、稳定排序并返回 ``stars > 9999`` 的前十仓库。"""

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GitHubRankingError("GitHub repository search did not return valid JSON") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise GitHubRankingError("GitHub repository search response has no items list")
    if payload.get("incomplete_results") is True:
        raise GitHubRankingError("GitHub repository search response is marked incomplete")
    valid: list[tuple[int, str, int]] = []
    for source_order, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = item.get("full_name")
        count = item.get("stargazers_count")
        if (
            isinstance(name, str)
            and name.strip()
            and isinstance(count, int)
            and not isinstance(count, bool)
            and count > 9_999
        ):
            valid.append((source_order, name.strip(), count))
    # Python 排序稳定；星标相同时保留 GitHub 响应中的先后顺序。
    valid.sort(key=lambda item: -item[2])
    top = valid[:AI_ALGORITHM_COLLECTION_TOP_N]
    if len(top) != AI_ALGORITHM_COLLECTION_TOP_N:
        raise GitHubRankingError(
            f"GitHub repository search returned only {len(top)} valid top repositories"
        )
    if len({name.casefold() for _, name, _ in top}) != AI_ALGORITHM_COLLECTION_TOP_N:
        raise GitHubRankingError("GitHub repository search returned duplicate top repositories")
    return [
        GitHubRankedRepository(
            rank=rank,
            name=name,
            star=count // 1_000,
            star_unit="k",
            exact_stargazers_count=count,
        )
        for rank, (_, name, count) in enumerate(top, start=1)
    ]


def prepare_github_rank_document(
    document: SourceDocument,
    *,
    rank: int,
) -> tuple[SourceDocument, dict[str, Any]]:
    """把完整 API 快照裁成当前名次的一行 CSV，并返回确定性业务值。"""

    if document.error:
        raise GitHubRankingError(f"GitHub repository search acquisition failed: {document.error}")
    repositories = parse_github_top_repositories(document.text)
    if rank < 1 or rank > len(repositories):
        raise GitHubRankingError(f"GitHub ranking position {rank} is outside the top ten")
    repository = repositories[rank - 1]
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["rank", "name", "star", "star_unit", "exact_stargazers_count"])
    writer.writerow(
        [
            repository.rank,
            repository.name,
            repository.star,
            repository.star_unit,
            repository.exact_stargazers_count,
        ]
    )
    original_hash = document.content_hash or hashlib.sha256(document.text.encode()).hexdigest()
    document.text = output.getvalue().strip()
    document.title = "GitHub repositories with more than 9,999 stars, sorted by stars"
    document.snippet = (
        f"Rank {repository.rank}: {repository.name}, "
        f"{repository.exact_stargazers_count} exact stars = {repository.star}k"
    )
    # 工作表按用户要求统一记录可浏览的固定搜索页；requested/normalized_url 继续保留实际
    # API 获取地址，从而兼顾业务来源字段和内部下载审计。
    document.url = GITHUB_TOP_REPOSITORIES_SOURCE_URL
    document.content_hash = original_hash
    return document, {
        "name": repository.name,
        "star": repository.star,
        "exact_stargazers_count": repository.exact_stargazers_count,
    }
