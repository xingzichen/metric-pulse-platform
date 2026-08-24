"""福布斯 AI 50 官方快照的确定性解析、融资换算和逐公司证据切片。

福布斯列表页把完整 50 条名单放在 ``__NEXT_DATA__`` 中。生产管线只保留当前业务需要的
字段，先校验年度、数量、内部位置和名称唯一性，再把一家公司切成单行 CSV 交给两阶段模型。
页面位置仅用于切片，不代表榜单名次。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .dataset_profiles import FORBES_AI50_SOURCE_URL, TOP_LIST_AI_COUNT


class ForbesAI50Error(ValueError):
    """官方快照缺失、不完整或违反年度名单契约。"""


@dataclass(frozen=True, slots=True)
class ForbesAI50Company:
    """一家公司在官方年度快照中的规范化事实。"""

    list_position: int
    company_name: str
    headquarter_location_raw: str
    ceo: str | None
    funding_raw: str
    funding_millions: Decimal
    financing_amount_yi: Decimal
    establish_date: int
    description: str
    profile_url: str


@dataclass(frozen=True, slots=True)
class ForbesAI50Snapshot:
    """经过严格完整性校验的年度 50 家公司快照。"""

    year: int
    published_at: str
    companies: tuple[ForbesAI50Company, ...]


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f") if normalized != normalized.to_integral() else str(int(normalized))


def _json_number(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral() else float(value)


def funding_millions_to_yi(value: Any) -> Decimal:
    """把福布斯以百万美元保存的融资额确定性换算为亿美元。"""

    try:
        parsed = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ForbesAI50Error(f"invalid Forbes funding value: {value!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise ForbesAI50Error(f"invalid Forbes funding value: {value!r}")
    return parsed * Decimal("0.01")


def format_forbes_funding(value_millions: Decimal) -> str:
    """按福布斯页面的 MIL/BIL 习惯生成可核验的官方融资原文表示。"""

    if value_millions >= 1_000:
        return f"${_decimal_text(value_millions / Decimal('1000'))} BIL"
    return f"${_decimal_text(value_millions)} MIL"


def _next_payload(html_text: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    content = script.string or script.get_text() if script else None
    if not content:
        raise ForbesAI50Error("Forbes page has no __NEXT_DATA__ payload")
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ForbesAI50Error("Forbes __NEXT_DATA__ payload is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ForbesAI50Error("Forbes __NEXT_DATA__ payload is not an object")
    return payload


def _table_and_meta(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    page_props = payload.get("props", {}).get("pageProps", {})
    blocks = page_props.get("schema", {}).get("blocks", [])
    if not isinstance(blocks, list):
        raise ForbesAI50Error("Forbes page has no schema blocks")
    tables = [
        block.get("data")
        for block in blocks
        if isinstance(block, dict)
        and block.get("component") == "Table"
        and isinstance(block.get("data"), dict)
        and block["data"].get("listUri") == "ai50"
    ]
    if len(tables) != 1:
        raise ForbesAI50Error(f"Forbes page contains {len(tables)} AI 50 tables")
    meta = page_props.get("meta")
    if not isinstance(meta, dict):
        raise ForbesAI50Error("Forbes page has no publication metadata")
    return tables[0], meta


def compact_forbes_ai50_html(html_text: str) -> str | None:
    """把列表页的大型 Next.js 状态压缩为可缓存的业务快照 JSON。

    非福布斯 AI 50 页面返回 ``None``，让通用 HTML 主文提取流程保持原行为。
    """

    if "__NEXT_DATA__" not in html_text or "ai50" not in html_text.casefold():
        return None
    payload = _next_payload(html_text)
    table, meta = _table_and_meta(payload)
    rows = table.get("dataRows")
    if not isinstance(rows, list):
        raise ForbesAI50Error("Forbes AI 50 table has no data rows")
    compact = {
        "profile": "forbes_ai50_official_snapshot_v1",
        "year": table.get("year"),
        "total_count": table.get("totalCount"),
        "published_at": meta.get("datetime"),
        "title": meta.get("title"),
        "source_url": FORBES_AI50_SOURCE_URL,
        "funding_format": {"currency": "USD", "magnitude": "Millions"},
        "companies": [
            {
                "list_position": row.get("position"),
                "rank": row.get("rank"),
                "company_name": row.get("organizationName") or row.get("name"),
                "CEO": row.get("ceoName"),
                "city": row.get("city"),
                "state": row.get("state"),
                "country": row.get("country"),
                "funding_millions": row.get("funding"),
                "establish_date": row.get("yearFounded"),
                "description": row.get("description"),
                "profile_uri": row.get("uri"),
            }
            for row in rows
            if isinstance(row, dict)
        ],
    }
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"))


def _parse_publication(value: Any, expected_year: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ForbesAI50Error("Forbes AI 50 publication time is missing")
    formats = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %I:%M%p")
    parsed: datetime | None = None
    for pattern in formats:
        try:
            parsed = datetime.strptime(value.strip(), pattern)
            break
        except ValueError:
            continue
    if parsed is None or parsed.year != expected_year:
        raise ForbesAI50Error(f"Forbes publication time does not match {expected_year}: {value!r}")
    return parsed.replace(tzinfo=ZoneInfo("America/New_York")).isoformat(timespec="seconds")


def parse_forbes_ai50_snapshot(text: str, *, expected_year: int) -> ForbesAI50Snapshot:
    """解析压缩 JSON 或原始列表 HTML，并严格验证当前年度 50 条名单。"""

    compact = compact_forbes_ai50_html(text) if "<html" in text[:500].casefold() else text
    if not compact:
        raise ForbesAI50Error("input is not a Forbes AI 50 snapshot")
    try:
        payload = json.loads(compact)
    except json.JSONDecodeError as exc:
        raise ForbesAI50Error("Forbes AI 50 snapshot is invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("profile") != "forbes_ai50_official_snapshot_v1":
        raise ForbesAI50Error("input is not the supported Forbes AI 50 snapshot format")
    try:
        year = int(payload.get("year"))
    except (TypeError, ValueError) as exc:
        raise ForbesAI50Error("Forbes AI 50 year is invalid") from exc
    if year != expected_year:
        raise ForbesAI50Error(f"Forbes AI 50 year {year} does not match requested {expected_year}")
    rows = payload.get("companies")
    if payload.get("total_count") != TOP_LIST_AI_COUNT or not isinstance(rows, list):
        raise ForbesAI50Error("Forbes AI 50 snapshot does not declare exactly 50 companies")
    if len(rows) != TOP_LIST_AI_COUNT:
        raise ForbesAI50Error(f"Forbes AI 50 snapshot contains {len(rows)} companies")

    companies: list[ForbesAI50Company] = []
    for expected_position, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or row.get("list_position") != expected_position:
            raise ForbesAI50Error("Forbes AI 50 positions are not a complete ordered 1..50 sequence")
        if row.get("rank") not in (None, ""):
            raise ForbesAI50Error("Forbes AI 50 unexpectedly contains ranks; unranked contract changed")
        name = str(row.get("company_name") or "").strip()
        city = str(row.get("city") or "").strip()
        state = str(row.get("state") or "").strip()
        country = str(row.get("country") or "").strip()
        description = re.sub(r"\s+", " ", str(row.get("description") or "")).strip()
        if not name or not city or not country or not description:
            raise ForbesAI50Error(f"Forbes company at position {expected_position} lacks identity/HQ text")
        try:
            founded = int(row.get("establish_date"))
        except (TypeError, ValueError) as exc:
            raise ForbesAI50Error(f"Forbes company {name!r} has invalid founded year") from exc
        try:
            funding_millions = Decimal(str(row.get("funding_millions")))
        except (InvalidOperation, AttributeError) as exc:
            raise ForbesAI50Error(f"Forbes company {name!r} has invalid funding") from exc
        if not funding_millions.is_finite() or funding_millions < 0:
            raise ForbesAI50Error(f"Forbes company {name!r} has invalid funding")
        profile_uri = str(row.get("profile_uri") or "").strip().strip("/")
        profile_url = (
            f"https://www.forbes.com/companies/{profile_uri}/?list=ai50"
            if profile_uri
            else FORBES_AI50_SOURCE_URL
        )
        hq = ", ".join(part for part in (city, state, country) if part)
        companies.append(
            ForbesAI50Company(
                list_position=expected_position,
                company_name=name,
                headquarter_location_raw=hq,
                ceo=str(row.get("CEO") or "").strip() or None,
                funding_raw=format_forbes_funding(funding_millions),
                funding_millions=funding_millions,
                financing_amount_yi=funding_millions_to_yi(funding_millions),
                establish_date=founded,
                description=description,
                profile_url=profile_url,
            )
        )
    if len({company.company_name.casefold() for company in companies}) != TOP_LIST_AI_COUNT:
        raise ForbesAI50Error("Forbes AI 50 snapshot contains duplicate company names")
    return ForbesAI50Snapshot(
        year=year,
        published_at=_parse_publication(payload.get("published_at"), expected_year),
        companies=tuple(companies),
    )


def prepare_forbes_ai50_company_document(
    document: Any,
    *,
    list_position: int,
    expected_year: int,
) -> tuple[Any, dict[str, Any]]:
    """把完整官方快照裁成一家公司的一行证据，并返回程序拥有的确定性字段。"""

    if document.error:
        raise ForbesAI50Error(f"Forbes AI 50 acquisition failed: {document.error}")
    snapshot = parse_forbes_ai50_snapshot(document.text, expected_year=expected_year)
    if list_position < 1 or list_position > len(snapshot.companies):
        raise ForbesAI50Error(f"Forbes AI 50 position {list_position} is outside 1..50")
    company = snapshot.companies[list_position - 1]
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "list_position",
            "rank_year",
            "company_name",
            "headquarter_location_raw",
            "CEO",
            "funding_official",
            "funding_millions",
            "financing_amount",
            "financing_amount_unit",
            "establish_date",
            "official_description",
        ]
    )
    writer.writerow(
        [
            company.list_position,
            snapshot.year,
            company.company_name,
            company.headquarter_location_raw,
            company.ceo,
            company.funding_raw,
            _decimal_text(company.funding_millions),
            _decimal_text(company.financing_amount_yi),
            "亿美元",
            company.establish_date,
            company.description,
        ]
    )
    original_hash = document.content_hash or hashlib.sha256(document.text.encode()).hexdigest()
    document.text = output.getvalue().strip()
    document.title = f"Forbes {snapshot.year} AI 50: {company.company_name}"
    document.snippet = (
        f"Official list position {company.list_position}; {company.company_name}; "
        f"{company.funding_raw} = {_decimal_text(company.financing_amount_yi)} 亿美元"
    )
    document.url = FORBES_AI50_SOURCE_URL
    document.content_hash = original_hash
    return document, {
        "list_position": company.list_position,
        "rank_year": snapshot.year,
        "company_name": company.company_name,
        "headquarter_location_raw": company.headquarter_location_raw,
        "CEO": company.ceo,
        "funding_raw": company.funding_raw,
        "funding_millions": _json_number(company.funding_millions),
        "financing_amount": _json_number(company.financing_amount_yi),
        "financing_amount_unit": "亿美元",
        "funding_formula": (
            f"{_decimal_text(company.funding_millions)} 百万美元 x 0.01 = "
            f"{_decimal_text(company.financing_amount_yi)} 亿美元"
        ),
        "establish_date": company.establish_date,
        "datasource_date": snapshot.published_at,
        "profile_url": company.profile_url,
    }
