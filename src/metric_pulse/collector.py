"""单行数据采集与证据核验主流程。

核心原则：有输入链接时优先读取链接，无法定位时才搜索；每个单元固定执行候选提取和独立
核验；搜索结果只是候选证据，只有被核验明确引用的文档才能回填 ``source_url``。网页内容
始终是不可信输入，不能改变系统指令或绕过行契约的地区、时间、口径和单位约束。结构化
表格优先做确定性精确匹配，模型负责理解与复核，而不是代替可验证的程序判断。
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import html
import ipaddress
import json
import re
import socket
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from .config import get_settings
from .models import CollectionUnit, DataRecord
from .omlx import OMLXClient
from .source_pipeline import (
    SourceDocument,
    build_contact_sheet,
    gather_source_documents,
    normalize_source_url,
)

_SEARCH_THROTTLE_LOCK = asyncio.Lock()
_LAST_SEARCH_STARTED_AT = 0.0


@dataclass(slots=True)
class EvidenceItem:
    """一条可展示、可审计的来源证据；是否被采用记录在 metadata 中。"""

    source_url: str | None = None
    title: str | None = None
    locator: str | None = None
    excerpt: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class CollectionResult:
    """采集器交给处理器的完整结果包，而不只是最终字段值。

    ``search_attempt``、``acquisition_attempt`` 和 ``model_calls`` 由处理器分别持久化，
    以便后续解释为什么走搜索、模型调用了几次以及最终采用了哪个来源。
    """

    values: dict[str, Any]
    evidence: list[EvidenceItem] = field(default_factory=list)
    validation: dict[str, Any] = field(default_factory=lambda: {"valid": True, "errors": []})
    model: str | None = None
    search_attempt: dict[str, Any] | None = None
    acquisition_attempt: dict[str, Any] | None = None
    model_calls: list[dict[str, Any]] = field(default_factory=list)


class Collector(Protocol):
    """处理器依赖的最小采集接口，便于测试使用确定性替身。"""

    async def collect(self, record: DataRecord, unit: CollectionUnit) -> CollectionResult: ...


def _descriptor_tokens(descriptors: dict[str, Any]) -> set[str]:
    """生成用于正文相关性定位的描述字段词元，并拆分季度为年份和季度号。"""

    tokens: set[str] = set()
    for value in descriptors.values():
        if value in (None, ""):
            continue
        text = str(value).strip().lower()
        if len(text) >= 2:
            tokens.add(text)
        match = re.fullmatch(r"(\d{4})q([1-4])", text)
        if match:
            tokens.update(match.groups())
    return tokens


def _normalized_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def _structured_constraints(header: list[str], descriptors: dict[str, Any]) -> list[tuple[int, str, str]]:
    """把行描述字段映射成 CSV 列位置和值约束。

    地区和时间存在常见别名，必须先做显式映射；只有表头真实存在的描述字段才参与匹配，
    从而避免用一个不存在的列把所有候选行都错误排除。
    """

    header_map = {_normalized_cell(name): index for index, name in enumerate(header)}
    constraints: list[tuple[int, str, str]] = []

    def add(header_names: tuple[str, ...], expected: Any, label: str) -> bool:
        if expected in (None, ""):
            return False
        for name in header_names:
            if name in header_map:
                constraints.append((header_map[name], _normalized_cell(expected), label))
                return True
        return False

    period = descriptors.get("statistical_date")
    period_match = re.fullmatch(r"\s*(\d{4})\s*[Qq]([1-4])\s*", str(period or ""))
    if period_match:
        add(("year", "年份"), period_match.group(1), "year")
        add(("quarter", "季度", "q"), period_match.group(2), "quarter")
    else:
        add(("statistical_date", "date", "period", "统计时间", "统计期"), period, "period")

    location_aliases = {
        "region": ("region", "iso2_code", "iso2", "country_code", "country", "地区", "国家"),
        "country": ("country", "country_name", "iso2_code", "country_code", "国家"),
        "province": ("province", "state", "省", "省份"),
        "city": ("city", "城市", "市"),
        "district": ("district", "区县", "区"),
        "other_region": ("other_region", "region", "地区"),
    }
    consumed = {"statistical_date"}
    for key, aliases in location_aliases.items():
        if add(aliases, descriptors.get(key), key):
            consumed.add(key)
    for key, value in descriptors.items():
        normalized_key = _normalized_cell(key)
        if key in consumed or normalized_key not in header_map or value in (None, ""):
            continue
        constraints.append((header_map[normalized_key], _normalized_cell(value), key))
    return constraints


def structured_match_diagnostics(text: str, descriptors: dict[str, Any]) -> dict[str, Any]:
    """诊断 CSV/TSV 中符合行契约的记录数量，不直接猜测业务值。

    UNIQUE_MATCH 才允许确定性取值；AMBIGUOUS_MATCH 和 TARGET_NOT_FOUND 会触发搜索降级或
    留给模型判断，并把约束细节写入采集审计。
    """

    lines = text.splitlines()
    if len(lines) < 2 or ("," not in lines[0] and "\t" not in lines[0]):
        return {"status": "NOT_STRUCTURED", "match_count": 0}
    delimiter = "\t" if lines[0].count("\t") > lines[0].count(",") else ","
    try:
        header = next(csv.reader([lines[0]], delimiter=delimiter))
    except csv.Error, StopIteration:
        return {"status": "PARSE_FAILED", "match_count": 0}
    constraints = _structured_constraints(header, descriptors)
    if not constraints:
        return {
            "status": "NO_MATCH_KEYS",
            "match_count": 0,
            "header": header,
            "delimiter": delimiter,
        }
    matches: list[tuple[list[str], str]] = []
    for line in lines[1:]:
        try:
            cells = next(csv.reader([line], delimiter=delimiter))
        except csv.Error, StopIteration:
            continue
        if all(
            position < len(cells) and _normalized_cell(cells[position]) == expected
            for position, expected, _ in constraints
        ):
            matches.append((cells, line))
    status = "UNIQUE_MATCH" if len(matches) == 1 else ("AMBIGUOUS_MATCH" if matches else "TARGET_NOT_FOUND")
    return {
        "status": status,
        "match_count": len(matches),
        "header": header,
        "delimiter": delimiter,
        "matches": matches,
        "constraints": [
            {"header": header[position], "expected": expected, "descriptor": label}
            for position, expected, label in constraints
        ],
        "constraint_indices": [position for position, _, _ in constraints],
    }


def _matched_csv_rows(
    text: str, descriptors: dict[str, Any]
) -> tuple[list[str], list[tuple[list[str], str]]] | None:
    diagnostics = structured_match_diagnostics(text, descriptors)
    if diagnostics["status"] not in {"UNIQUE_MATCH", "AMBIGUOUS_MATCH"}:
        return None
    return diagnostics["header"], diagnostics["matches"]


def _number(value: str) -> int | float | None:
    normalized = value.strip().replace(",", "")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", normalized):
        return None
    parsed = float(normalized)
    return int(parsed) if parsed.is_integer() else parsed


def extract_structured_values(
    text: str, descriptors: dict[str, Any], target_fields: list[str]
) -> tuple[dict[str, Any], str] | None:
    """从唯一匹配行提取全部业务目标字段，并返回可复核的“表头+原行”摘录。

    当业务列没有直接使用目标字段名时，仅在排除键列后恰好剩下一个数值列的情况下，才把
    该数值映射给 data/be_data/value/result，避免从多指标行中任取一个数字。
    """

    matched = _matched_csv_rows(text, descriptors)
    if matched is None:
        return None
    header, rows = matched
    if len(rows) != 1:
        return None
    cells, source_line = rows[0]
    header_map = {name.strip().lower(): index for index, name in enumerate(header)}
    values: dict[str, Any] = {}
    for target_field in target_fields:
        if target_field.lower() in header_map and header_map[target_field.lower()] < len(cells):
            raw_value = cells[header_map[target_field.lower()]]
            values[target_field] = _number(raw_value) if _number(raw_value) is not None else raw_value

    diagnostics = structured_match_diagnostics(text, descriptors)
    constraint_indices = set(diagnostics.get("constraint_indices", []))
    excluded_headers = {"year", "quarter", "month", "date", "rank", "id", "logic_id"}
    unmatched_numbers = [
        parsed
        for index, cell in enumerate(cells)
        if index < len(header)
        and index not in constraint_indices
        and header[index].strip().lower() not in excluded_headers
        and (parsed := _number(cell)) is not None
    ]
    value_fields = {"be_data", "data", "value", "result"}
    if len(unmatched_numbers) == 1:
        for target_field in target_fields:
            if target_field.lower() in value_fields and target_field not in values:
                values[target_field] = unmatched_numbers[0]
    if set(values) != set(target_fields):
        return None
    return values, ",".join(header) + "\n" + source_line


def focus_evidence(text: str, descriptors: dict[str, Any], *, max_chars: int = 6_000) -> str:
    """在模型上下文预算内保留表结构或描述字段附近的证据窗口。"""
    if len(text) <= max_chars:
        return text
    tokens = _descriptor_tokens(descriptors)
    matched = _matched_csv_rows(text, descriptors)
    if matched is not None:
        header, rows = matched
        return (",".join(header) + "\n" + "\n".join(line for _, line in rows))[:max_chars]

    plain = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", text)
    plain = html.unescape(re.sub(r"(?s)<[^>]+>", "\n", plain))
    plain = "\n".join(line.strip() for line in plain.splitlines() if line.strip())
    lowered = plain.lower()
    positions = sorted({position for token in tokens if (position := lowered.find(token)) >= 0})
    if not positions:
        return plain[:max_chars]
    windows = [plain[max(0, position - 350) : position + 650] for position in positions]
    return "\n---\n".join(windows)[:max_chars]


async def fetch_public_text(url: str, *, max_bytes: int = 2_000_000) -> str:
    """兼容旧调用的受限文本下载；重定向前后都执行公共地址校验。"""

    await validate_public_url(url)
    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        response = await client.get(url, headers={"User-Agent": "MetricPulse/1.0"})
        response.raise_for_status()
        await validate_public_url(str(response.url))
        content = response.content[:max_bytes]
        return content.decode(response.encoding or "utf-8", errors="replace")


async def validate_public_url(url: str) -> None:
    """拒绝私网、回环和保留地址，防止来源 URL 被用于 SSRF。

    域名的所有 DNS 结果都必须可接受；配置的代理网段仅用于明确部署场景，不是通用私网
    放行。重定向后的最终 URL 仍会再次调用本函数。
    """

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    try:
        literal_ip = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None and not literal_ip.is_global:
        raise ValueError("Private, loopback, and reserved evidence addresses are not allowed")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
    except socket.gaierror as exc:
        raise ValueError("Evidence host cannot be resolved") from exc
    proxy_networks = tuple(
        ipaddress.ip_network(value.strip())
        for value in get_settings().ssrf_proxy_networks.split(",")
        if value.strip()
    )
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global and not any(ip in network for network in proxy_networks):
            raise ValueError("Private, loopback, and reserved evidence addresses are not allowed")


async def discover_source(query: str) -> tuple[str | None, str | None]:
    """Discover one public source through an optional SearXNG-compatible endpoint."""
    results = await discover_sources(query, limit=1)
    if not results:
        return None, None
    return results[0].source_url, results[0].title


async def discover_sources(query: str, *, limit: int = 10) -> list[EvidenceItem]:
    """搜索并返回经过 URL 安全校验的候选结果和摘要。

    全局锁和最小间隔保护本地 SearXNG/上游搜索引擎；指数退避只重试网络错误。这里返回的
    URL 仍是候选，不能直接写入生产字段。
    """
    search_url = get_settings().search_url
    if not search_url or not query.strip():
        return []
    settings = get_settings()
    global _LAST_SEARCH_STARTED_AT
    async with _SEARCH_THROTTLE_LOCK:
        elapsed = time.monotonic() - _LAST_SEARCH_STARTED_AT
        if elapsed < settings.search_min_interval_seconds:
            await asyncio.sleep(settings.search_min_interval_seconds - elapsed)
        async with httpx.AsyncClient(timeout=settings.search_timeout_seconds) as client:
            for attempt in range(3):
                _LAST_SEARCH_STARTED_AT = time.monotonic()
                try:
                    response = await client.get(
                        search_url,
                        params={
                            "q": query,
                            "format": "json",
                            "language": "zh-CN",
                            "safesearch": 0,
                        },
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(settings.search_retry_delay_seconds * (2**attempt))
    discovered: list[EvidenceItem] = []
    for item in response.json().get("results", []):
        candidate = item.get("url")
        if not isinstance(candidate, str):
            continue
        try:
            await validate_public_url(candidate)
        except ValueError:
            continue
        title = item.get("title") if isinstance(item.get("title"), str) else None
        snippet = item.get("content") if isinstance(item.get("content"), str) else None
        if snippet:
            snippet = html.unescape(re.sub(r"(?s)<[^>]+>", " ", snippet))
            snippet = re.sub(r"\s+", " ", snippet).strip()
        discovered.append(
            EvidenceItem(
                source_url=candidate,
                title=title,
                excerpt=snippet,
                metadata={
                    "provider": "searxng",
                    "engines": item.get("engines") or [item.get("engine")],
                },
            )
        )
        if len(discovered) >= limit:
            break
    return discovered


def render_search_evidence(items: list[EvidenceItem], *, max_chars: int = 5_000) -> str:
    """把搜索候选整理成带序号的短文本，保留 URL 与摘要对应关系。"""

    blocks = []
    for index, item in enumerate(items, start=1):
        blocks.append(
            "\n".join(
                part
                for part in (
                    f"Result {index}: {item.title or 'Untitled'}",
                    f"URL: {item.source_url}" if item.source_url else None,
                    f"Snippet: {item.excerpt}" if item.excerpt else None,
                )
                if part
            )
        )
    return "\n\n".join(blocks)[:max_chars]


def apply_source_provenance(
    values: dict[str, Any],
    target_fields: list[str],
    *,
    source_url: str | None,
    source_title: str | None,
) -> dict[str, Any]:
    """用应用已确认的文档元数据补齐来源字段，不覆盖已有显式值。"""

    if "source_url" in target_fields and values.get("source_url") in (None, ""):
        values["source_url"] = source_url
    if "source" in target_fields and values.get("source") in (None, ""):
        hostname = urlparse(source_url).hostname if source_url else None
        values["source"] = source_title or hostname
    return values


def apply_verification(
    verification: dict[str, Any],
    target_fields: list[str],
    value_target_fields: list[str],
) -> tuple[dict[str, Any], bool]:
    """应用独立核验结果，并清除未经证据确认的值。

    ``source``/``source_url`` 无条件先清空，因为来源归属由应用根据核验引用序号决定，
    绝不能信任模型在 values 中生成的链接。业务值在核验拒绝时也全部清空。
    """

    approved = verification.get("approved") is True
    raw_values = verification.get("values")
    if not isinstance(raw_values, dict):
        raw_values = {}
    values = {field: raw_values.get(field) for field in target_fields}
    # Provenance is application-owned. Even an otherwise approved model response
    # cannot supply a URL or source name unless it cites one fetched document.
    for provenance_field in {"source", "source_url"} & set(target_fields):
        values[provenance_field] = None
    if not approved:
        # A rejected extraction has no confirmed fact-to-source relationship.
        # Keep candidate URLs in Evidence only; target values, including provenance,
        # stay empty until automatic or human validation succeeds.
        for field in value_target_fields:
            values[field] = None
    return values, approved


def render_source_documents(
    documents: list[SourceDocument],
    descriptors: dict[str, Any],
    *,
    max_chars: int = 30_000,
) -> str:
    """将规范化文档渲染为模型可引用的编号来源集合。"""

    blocks: list[str] = []
    for document in documents:
        main_content = focus_evidence(document.text, descriptors, max_chars=2_500)
        parts = [
            f"SOURCE [{document.index}]",
            f"Title: {document.title or 'Untitled'}",
            f"URL: {document.url}",
            f"Type: {document.media_type}",
        ]
        if document.browser_rendered:
            parts.append(f"Acquisition: Playwright-rendered ({document.browser_fallback_reason})")
        if document.snippet:
            parts.append(f"Search snippet: {document.snippet[:800]}")
        if main_content:
            parts.append("Main content:\n" + main_content)
        if document.images:
            parts.append(f"Visual evidence: {len(document.images)} image/page(s) in attached contact sheet")
        if document.error:
            parts.append(f"Fetch note: {document.error}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)[:max_chars]


def evidence_from_documents(
    documents: list[SourceDocument],
    *,
    selected_indices: set[int] | None = None,
    confidence: Any = None,
    conflicts: Any = None,
    acquisition_route: str | None = None,
) -> list[EvidenceItem]:
    """把内部 SourceDocument 转为长期保存的证据记录，并标记被核验选中的来源。"""

    selected_indices = selected_indices or set()
    return [
        EvidenceItem(
            source_url=document.url,
            title=document.title,
            excerpt=(document.text or document.snippet or "")[:1_500] or None,
            metadata={
                "provider": "direct-source" if acquisition_route == "DIRECT_LINK" else "searxng-multi-source",
                "acquisition_route": acquisition_route,
                "rank": document.index,
                "media_type": document.media_type,
                "image_count": len(document.images),
                "browser_rendered": document.browser_rendered,
                "browser_fallback_reason": document.browser_fallback_reason,
                "http_status": document.http_status,
                "selected": document.index in selected_indices,
                "fetch_error": document.error,
                "cache_hit": document.cache_hit,
                "persistent_cache_hit": document.persistent_cache_hit,
                "normalized_url": document.normalized_url,
                "content_hash": document.content_hash,
                "confidence": confidence if document.index in selected_indices else None,
                "conflicts": conflicts if document.index in selected_indices else None,
            },
        )
        for document in documents
    ]


def evaluate_direct_documents(
    documents: list[SourceDocument],
    descriptors: dict[str, Any],
    value_target_fields: list[str],
) -> tuple[bool, str | None, dict[str, Any]]:
    """判断输入链接是否足以直接进入模型处理，还是必须降级搜索。

    结构化来源要求唯一精确匹配；非结构化正文至少要出现行描述词元并含数值，纯来源字段
    任务可不要求数字。图片证据可交给视觉模型复核，但抓取错误不能算作直链成功。
    """

    usable = [document for document in documents if not document.error and (document.text or document.images)]
    if not usable:
        return False, "DIRECT_FETCH_FAILED", {"match_status": "FETCH_FAILED", "match_count": 0}

    structured_seen = False
    best_diagnostics: dict[str, Any] = {"match_status": "UNSTRUCTURED", "match_count": 0}
    for document in usable:
        diagnostics = structured_match_diagnostics(document.text, descriptors)
        status = diagnostics["status"]
        if status == "NOT_STRUCTURED":
            continue
        structured_seen = True
        best_diagnostics = {
            "match_status": status,
            "match_count": diagnostics.get("match_count", 0),
            "match_constraints": diagnostics.get("constraints", []),
        }
        if status == "UNIQUE_MATCH":
            if (
                not value_target_fields
                or extract_structured_values(
                    document.text,
                    descriptors,
                    value_target_fields,
                )
                is not None
            ):
                return True, None, best_diagnostics
            return False, "DIRECT_SOURCE_INCOMPLETE", best_diagnostics
        if status == "AMBIGUOUS_MATCH":
            return False, "AMBIGUOUS_MATCH", best_diagnostics

    if structured_seen:
        reason = (
            "TARGET_NOT_FOUND"
            if best_diagnostics.get("match_status") == "TARGET_NOT_FOUND"
            else "DIRECT_SOURCE_INCOMPLETE"
        )
        return False, reason, best_diagnostics

    tokens = _descriptor_tokens(descriptors)
    for document in usable:
        if document.images:
            return True, None, best_diagnostics
        lowered = document.text.casefold()
        relevant_tokens = {token for token in tokens if token in lowered}
        if relevant_tokens and (re.search(r"\d", document.text) or not value_target_fields):
            return (
                True,
                None,
                {
                    "match_status": "UNSTRUCTURED_RELEVANT",
                    "match_count": 1,
                    "matched_tokens": sorted(relevant_tokens)[:20],
                },
            )
    return False, "TARGET_NOT_FOUND", best_diagnostics


def build_search_query(record: DataRecord) -> str:
    """从行契约构造紧凑搜索词，优先指标身份、地区、时间、行业和单位。

    搜索词不包含源行号或整行 JSON，减少泄漏无关字段，也避免搜索引擎被噪声描述干扰。
    """

    descriptors = record.row_contract.get("descriptors", {})
    identity_keys = (
        "index_name",
        "title",
        "product_name",
        "company_name",
        "project",
        "manufacturer",
        "model",
        "frame_type",
        "rank_name",
    )
    location_keys = ("country", "region", "province", "city", "district", "other_region")
    date_keys = ("statistical_date", "rank_year", "publish_time", "collect_date", "issue_date")
    parts: list[str] = []
    identity = next(
        (str(descriptors[key]).strip() for key in identity_keys if descriptors.get(key) not in (None, "")),
        "",
    )
    if identity:
        parts.append(f'"{identity}"')
    for keys in (location_keys, date_keys):
        for key in keys:
            value = descriptors.get(key)
            if value not in (None, "") and str(value) not in parts:
                parts.append(str(value))
    industry = descriptors.get("industry")
    if industry not in (None, "") and str(industry) not in identity:
        parts.append(str(industry))
    unit = record.raw_data.get("unit") or record.raw_data.get("be_unit")
    if unit not in (None, ""):
        parts.append(str(unit))
    if not parts:
        parts.append(re.sub(r"\([^)]*\)|\uff08[^\uff09]*\uff09", "", record.sheet_name).strip())
    return " ".join(parts)


class OMLXCollector:
    """执行“直链优先/搜索降级 → 候选提取 → 独立核验”的生产采集器。"""

    def __init__(self, client: OMLXClient | None = None) -> None:
        self.client = client or OMLXClient()

    def _model_telemetry(self) -> dict[str, Any]:
        metadata = getattr(self.client, "last_response_metadata", {})
        return dict(metadata) if isinstance(metadata, dict) else {}

    async def collect(self, record: DataRecord, unit: CollectionUnit) -> CollectionResult:
        """采集一个单元并返回值、证据及完整审计元数据。

        每次调用只处理一行、使用新的模型消息；同源复用依赖 OMLX 前缀缓存和来源文档缓存，
        不复用可能混入其他行结论的对话历史。
        """

        descriptors = record.row_contract.get("descriptors", {})
        query = build_search_query(record)
        input_source_url = next(
            (
                value
                for key, value in record.raw_data.items()
                if key in {"source_url", "url", "link"} and isinstance(value, str) and value
            ),
            None,
        )
        settings = get_settings()
        value_target_fields = [field for field in unit.target_fields if field not in {"source", "source_url"}]

        async def gather(candidates: list[EvidenceItem]) -> list[SourceDocument]:
            return await gather_source_documents(
                candidates,
                validate_public_url,
                concurrency=settings.source_fetch_concurrency,
                browser_fallback_enabled=settings.browser_fallback_enabled,
                browser_timeout_seconds=settings.browser_timeout_seconds,
                browser_settle_seconds=settings.browser_settle_seconds,
                browser_min_content_chars=settings.browser_min_content_chars,
                browser_site_cooldown_seconds=settings.browser_site_cooldown_seconds,
            )

        # 第一阶段：优先验证工作簿提供的链接。直链失败原因会进入审计，并成为搜索降级依据。
        acquisition_started_at = datetime.now(UTC)
        search_attempt: dict[str, Any] | None = None
        direct_documents: list[SourceDocument] = []
        direct_succeeded = False
        fallback_reason: str | None = "NO_DIRECT_SOURCE"
        match_metadata: dict[str, Any] = {"match_status": "NO_DIRECT_SOURCE", "match_count": 0}
        if input_source_url:
            direct_documents = await gather(
                [
                    EvidenceItem(
                        source_url=input_source_url,
                        title="Workbook-provided source",
                        metadata={"provider": "workbook"},
                    )
                ]
            )
            direct_succeeded, fallback_reason, match_metadata = evaluate_direct_documents(
                direct_documents,
                descriptors,
                value_target_fields,
            )

        if direct_succeeded:
            route = "DIRECT_LINK"
            documents = direct_documents
        else:
            route = "SEARCH_FALLBACK"
            search_started_at = datetime.now(UTC)
            search_results = await discover_sources(query, limit=10)
            search_ended_at = datetime.now(UTC)
            search_attempt = {
                "query": query,
                "provider": "searxng",
                "status": "SUCCEEDED",
                "result_count": len(search_results),
                "results": [
                    {
                        "rank": index,
                        "url": item.source_url,
                        "title": item.title,
                    }
                    for index, item in enumerate(search_results, start=1)
                ],
                "started_at": search_started_at,
                "ended_at": search_ended_at,
            }
            documents = await gather(search_results)

        # 将来源获取过程与内容身份固化，缓存命中也必须能追溯到规范化 URL 和内容哈希。
        acquisition_ended_at = datetime.now(UTC)
        source_contexts = [
            {
                "source_context_id": hashlib.sha256(
                    f"{document.normalized_url or document.url}\n{document.content_hash or ''}".encode()
                ).hexdigest(),
                "requested_url": document.requested_url,
                "normalized_url": document.normalized_url,
                "final_url": document.url,
                "content_hash": document.content_hash,
                "cache_hit": document.cache_hit,
                "persistent_cache_hit": document.persistent_cache_hit,
                "media_type": document.media_type,
                "parser_version": document.parser_version,
            }
            for document in documents
        ]
        acquisition_attempt = {
            "route": route,
            "status": "SUCCEEDED",
            "reason": fallback_reason if route == "SEARCH_FALLBACK" else None,
            "input_url": input_source_url,
            "normalized_url": normalize_source_url(input_source_url) if input_source_url else None,
            "final_url": direct_documents[0].url if direct_documents else None,
            "cache_hit": any(document.cache_hit for document in documents),
            "persistent_cache_hit": any(document.persistent_cache_hit for document in documents),
            "content_hash": next(
                (document.content_hash for document in documents if document.content_hash),
                None,
            ),
            **match_metadata,
            "source_contexts": source_contexts,
            "started_at": acquisition_started_at,
            "ended_at": acquisition_ended_at,
        }
        # 确定性结构化候选和网页/附件正文会一起交给模型，模型不能忽略程序匹配结果。
        evidence_text = render_source_documents(documents, descriptors)
        contact_sheet = build_contact_sheet(documents) if get_settings().vision_analysis_enabled else None
        structured_matches: list[tuple[SourceDocument, dict[str, Any], str]] = []
        if value_target_fields:
            for document in documents:
                structured = extract_structured_values(
                    document.text,
                    descriptors,
                    value_target_fields,
                )
                if structured is not None:
                    values, excerpt = structured
                    structured_matches.append((document, values, excerpt))
        structured_candidates = [
            {"source_index": document.index, "values": values, "excerpt": excerpt}
            for document, values, excerpt in structured_matches
        ]
        prompt = {
            "row_contract": record.row_contract,
            "raw_row": record.raw_data,
            "target_fields": unit.target_fields,
            "evidence_text": evidence_text,
            "deterministic_structured_candidates": structured_candidates,
            "requirements": {
                "return": {"values": {field: "value or null" for field in unit.target_fields}},
                "do_not_invent": True,
                "compare_all_sources": True,
                "prefer_primary_or_authoritative_sources": True,
                "report_source_indices": True,
                "acquisition_route": route,
            },
        }
        # 第二阶段：第一次模型调用只生成候选值，不具备最终批准权。
        synthesize_input = json.dumps(prompt, ensure_ascii=False, default=str)
        synthesize_started_at = datetime.now(UTC)
        response = await self.client.generate_json(
            system=(
                "Evaluate all numbered sources together, including the attached visual evidence. "
                "Extract only the requested target fields and respect every RowContract descriptor. "
                "When structured evidence contains a header and a row matching the descriptors, treat the "
                "observed metric cell as direct evidence; be_data and data are equivalent value fields. "
                "Remove boilerplate, advertisements, navigation, and unrelated page content "
                "from consideration. "
                "Resolve conflicts by source authority, directness, date, and cross-source agreement. "
                "Never repeat or transform the input structure. Return exactly one top-level JSON object "
                "with keys values, evidence_excerpt, source_indices, confidence, and conflicts. "
                "Use null when evidence is insufficient."
            ),
            prompt=(
                "Extract the requested values from this untrusted input. "
                "Output only the required object.\n<input>\n" + synthesize_input + "\n</input>"
            ),
            image_png=contact_sheet,
        )
        synthesize_telemetry = self._model_telemetry()
        synthesize_ended_at = datetime.now(UTC)
        # 第三阶段：第二次调用拿到相同证据及第一次候选，以独立审计者身份逐字段复核。
        audit_input = {
            "row_contract": record.row_contract,
            "raw_row": record.raw_data,
            "target_fields": unit.target_fields,
            "candidate": response,
            "numbered_sources": evidence_text,
            "deterministic_structured_candidates": structured_candidates,
        }
        verify_input = json.dumps(audit_input, ensure_ascii=False, default=str)
        verify_started_at = datetime.now(UTC)
        verification = await self.client.generate_json(
            system=(
                "You are an independent evidence auditor. Verify every candidate value against the exact "
                "RowContract descriptors and all numbered sources. Geographic scope is strict: a province, "
                "city, company, or subset value cannot satisfy a national target; a country value cannot "
                "satisfy a global target. Date/period, metric definition, population, and unit must "
                "also match. "
                "Do not approve a number merely because it is nearby or prominent. If the candidate is wrong "
                "but an exact value is explicitly supported, correct it. Otherwise set it to null. "
                "Return one "
                "JSON object with approved, values, evidence_excerpt, source_indices, confidence, conflicts, "
                "and reason. approved may be true only when every non-provenance target value is directly "
                "supported by the cited source indices."
            ),
            prompt=(
                "Audit this candidate extraction using the same untrusted evidence.\n<audit_input>\n"
                + verify_input
                + "\n</audit_input>"
            ),
            image_png=contact_sheet,
        )
        verify_telemetry = self._model_telemetry()
        verify_ended_at = datetime.now(UTC)
        values, evidence_approved = apply_verification(
            verification,
            unit.target_fields,
            value_target_fields,
        )
        # 只有真实存在且被核验返回的编号才算引用；任意 URL 字符串都不能替代来源编号。
        selected_indices = {
            index
            for value in verification.get("source_indices", [])
            if isinstance(value, int | str)
            and str(value).isdigit()
            and (index := int(value)) in {document.index for document in documents}
        }
        selected_documents = [document for document in documents if document.index in selected_indices]
        selected_document = next(
            (
                document
                for document in selected_documents
                if document.text or document.snippet or document.images
            ),
            None,
        )
        display_document = selected_document or next(
            (document for document in documents if document.text or document.snippet),
            None,
        )
        # 最终来源门禁：核验通过且存在可用的被引用文档时，应用才写入来源字段。
        if evidence_approved and selected_document:
            values = apply_source_provenance(
                values,
                unit.target_fields,
                source_url=selected_document.url,
                source_title=selected_document.title,
            )
            if "source_url" in unit.target_fields:
                # The cited, fetched source is authoritative; never accept a model-invented URL.
                values["source_url"] = selected_document.url
        evidence = evidence_from_documents(
            documents,
            selected_indices=selected_indices,
            confidence=verification.get("confidence"),
            conflicts=verification.get("conflicts"),
            acquisition_route=route,
        )
        for document, structured_values, excerpt in structured_matches:
            evidence.append(
                EvidenceItem(
                    source_url=document.url,
                    title=document.title,
                    excerpt=excerpt,
                    metadata={
                        "provider": "deterministic-structured-candidate",
                        "source_index": document.index,
                        "candidate_values": structured_values,
                        "selected": document.index in selected_indices,
                    },
                )
            )
        if verification.get("evidence_excerpt"):
            evidence.append(
                EvidenceItem(
                    source_url=display_document.url if display_document else None,
                    title=display_document.title if display_document else None,
                    excerpt=verification.get("evidence_excerpt"),
                    metadata={
                        "provider": "omlx-verification",
                        "approved": evidence_approved,
                        "source_indices": sorted(selected_indices),
                        "confidence": verification.get("confidence"),
                        "conflicts": verification.get("conflicts"),
                        "reason": verification.get("reason"),
                    },
                )
            )
        errors = [field for field, value in values.items() if value in (None, "")]
        return CollectionResult(
            values=values,
            evidence=evidence,
            validation={
                "valid": evidence_approved and not errors,
                "missing_fields": errors,
                "evidence_approved": evidence_approved,
                "reason": verification.get("reason"),
            },
            model=get_settings().omlx_model,
            search_attempt=search_attempt,
            acquisition_attempt=acquisition_attempt,
            model_calls=[
                {
                    "phase": "SYNTHESIZE",
                    "model": get_settings().omlx_model,
                    "status": "SUCCEEDED",
                    "input_hash": hashlib.sha256(synthesize_input.encode()).hexdigest(),
                    "output_summary": {
                        "keys": sorted(response),
                        "provider": synthesize_telemetry,
                    },
                    "started_at": synthesize_started_at,
                    "ended_at": synthesize_ended_at,
                },
                {
                    "phase": "VERIFY",
                    "model": get_settings().omlx_model,
                    "status": "SUCCEEDED",
                    "input_hash": hashlib.sha256(verify_input.encode()).hexdigest(),
                    "output_summary": {
                        "approved": verification.get("approved") is True,
                        "keys": sorted(verification),
                        "provider": verify_telemetry,
                    },
                    "started_at": verify_started_at,
                    "ended_at": verify_ended_at,
                },
            ],
        )


def configured_collector() -> Collector:
    """构造生产采集器；测试通过依赖注入替换，而不是在生产代码中读取对照数据。"""

    return OMLXCollector()
