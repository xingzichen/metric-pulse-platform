"""网页、CSV、PDF、Word 和图片来源的获取与规范化。

管线先尝试受限 HTTP 下载，再按媒体类型提取正文；遇到挑战页、内容不足或特定状态码时才
启用浏览器渲染。所有下载均受大小、页数、图片数量和并发限制，并经过公共地址校验。缓存
只减少来源获取成本，不改变逐行双模型核验要求。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
import pymupdf
import trafilatura
from bs4 import BeautifulSoup, Tag
from charset_normalizer import from_bytes
from docx import Document
from PIL import Image, ImageDraw, ImageOps

from .config import get_settings

MAX_DOCUMENT_BYTES = 20_000_000
MAX_DOCUMENT_CHARS = 80_000
MAX_PDF_PAGES = 50
MAX_IMAGES_PER_SOURCE = 2
MAX_VISION_IMAGES = 6
BOILERPLATE_TAGS = {
    "script",
    "style",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "iframe",
    "noscript",
    "svg",
    "button",
}
NOISY_IMAGE_PATTERN = re.compile(
    r"(?:logo|icon|avatar|banner|advert|tracking|pixel|spacer|sprite|wechat|qrcode|qr-code)",
    re.IGNORECASE,
)
CHALLENGE_PAGE_PATTERN = re.compile(
    r"(?:verify you are human|checking your browser|attention required|access denied|"
    r"unusual traffic|security check|captcha|cf-chl-|challenge-platform|"
    r"人机验证|安全验证|请输入验证码|访问过于频繁|异常访问|访问受限)",
    re.IGNORECASE,
)
BROWSER_RETRYABLE_STATUSES = {403, 406, 408, 409, 425, 429, 500, 502, 503, 504}
BROWSER_EXCLUDED_SUFFIXES = {
    ".7z",
    ".csv",
    ".doc",
    ".docx",
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".pdf",
    ".png",
    ".rar",
    ".svg",
    ".tsv",
    ".webp",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}
_SOURCE_CACHE: dict[str, SourceDocument] = {}
_SOURCE_CACHE_LOCKS: dict[str, asyncio.Lock] = {}
_SOURCE_CACHE_MAX_ITEMS = 2_048
_PARSER_VERSION = "source-pipeline-v2"
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
}


@dataclass(slots=True)
class ImageEvidence:
    """可送入视觉模型的一张规范化 PNG，并保留其来源编号。"""

    label: str
    png: bytes
    source_index: int


@dataclass(slots=True)
class SourceDocument:
    """一个候选来源经过下载、解析或浏览器渲染后的统一表示。

    ``requested_url`` 保留原始输入，``url`` 是最终跳转地址，``normalized_url`` 用于缓存键；
    三者分开保存，才能同时满足用户可追溯、内容去重和重定向审计。
    """

    index: int
    url: str
    requested_url: str | None = None
    title: str | None = None
    snippet: str | None = None
    media_type: str = "unknown"
    text: str = ""
    images: list[ImageEvidence] = field(default_factory=list)
    error: str | None = None
    http_status: int | None = None
    retry_after_seconds: float | None = None
    browser_rendered: bool = False
    browser_fallback_reason: str | None = None
    cache_hit: bool = False
    persistent_cache_hit: bool = False
    normalized_url: str | None = None
    content_hash: str | None = None
    parser_version: str = _PARSER_VERSION


class BrowserChallengeError(RuntimeError):
    """公开页面要求真人验证；采集器记录失败但不会尝试绕过。"""


def normalize_source_url(url: str) -> str:
    """生成稳定缓存 URL：去跟踪参数、片段，并把 GitHub blob 转为 raw 内容地址。"""

    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    path = parsed.path or "/"
    if host == "github.com":
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 5 and parts[2] == "blob":
            owner, repository, _, revision, *source_path = parts
            parsed = parsed._replace(
                scheme="https",
                netloc="raw.githubusercontent.com",
                path="/".join(("", owner, repository, revision, *source_path)),
                query="",
                fragment="",
            )
            host = "raw.githubusercontent.com"
            path = parsed.path
    port = parsed.port
    netloc = host if port is None else f"{host}:{port}"
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
        ],
        doseq=True,
    )
    return parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=netloc,
        path=path,
        query=query,
        fragment="",
    ).geturl()


def _persistent_cache_path(cache_key: str) -> Path:
    digest = hashlib.sha256(cache_key.encode()).hexdigest()
    return get_settings().source_cache_root / digest[:2] / f"{digest}.json"


def _load_persistent_document(cache_key: str, candidate: Any, index: int) -> SourceDocument | None:
    """读取未过期且解析器版本一致的跨任务缓存；损坏缓存按未命中处理。"""

    path = _persistent_cache_path(cache_key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError, TypeError:
        return None
    if payload.get("parser_version") != _PARSER_VERSION or payload.get("normalized_url") != cache_key:
        return None
    cached_at = payload.get("cached_at")
    if not isinstance(cached_at, int | float) or (
        time.time() - cached_at > get_settings().source_cache_ttl_seconds
    ):
        return None
    document = SourceDocument(
        index=index,
        url=payload.get("final_url") or cache_key,
        requested_url=candidate.source_url,
        title=candidate.title or payload.get("title"),
        snippet=candidate.excerpt or payload.get("snippet"),
        media_type=payload.get("media_type") or "unknown",
        text=payload.get("text") or "",
        http_status=payload.get("http_status"),
        browser_rendered=payload.get("browser_rendered") is True,
        browser_fallback_reason=payload.get("browser_fallback_reason"),
        cache_hit=True,
        persistent_cache_hit=True,
        normalized_url=cache_key,
        content_hash=payload.get("content_hash"),
    )
    return document if document.text else None


def _persist_document(cache_key: str, document: SourceDocument) -> None:
    """原子写入可复用文本缓存，失败或空内容永不缓存。"""

    if document.error or not document.text:
        return
    path = _persistent_cache_path(cache_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "parser_version": _PARSER_VERSION,
        "cached_at": time.time(),
        "normalized_url": cache_key,
        "final_url": document.url,
        "title": document.title,
        "snippet": document.snippet,
        "media_type": document.media_type,
        "text": document.text,
        "http_status": document.http_status,
        "browser_rendered": document.browser_rendered,
        "browser_fallback_reason": document.browser_fallback_reason,
        "content_hash": document.content_hash or hashlib.sha256(document.text.encode()).hexdigest(),
    }
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary_name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary_name)


def _cached_document(document: SourceDocument, candidate: Any, index: int) -> SourceDocument:
    cloned = copy.deepcopy(document)
    cloned.index = index
    cloned.requested_url = candidate.source_url
    cloned.title = candidate.title or cloned.title
    cloned.snippet = candidate.excerpt or cloned.snippet
    cloned.cache_hit = True
    cloned.normalized_url = normalize_source_url(candidate.source_url)
    for image in cloned.images:
        image.source_index = index
    return cloned


def _decode_text(data: bytes) -> str:
    match = from_bytes(data).best()
    return str(match) if match is not None else data.decode("utf-8", errors="replace")


def _normalize_text(text: str) -> str:
    lines = [re.sub(r"[ \f\v]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)[:MAX_DOCUMENT_CHARS]


def looks_like_challenge_page(text: str) -> bool:
    sample = text[:20_000]
    strong_markup = "cf-chl-" in sample.lower() or "challenge-platform" in sample.lower()
    return strong_markup or (len(text.strip()) < 6_000 and bool(CHALLENGE_PAGE_PATTERN.search(sample)))


def browser_fallback_reason(document: SourceDocument, *, min_content_chars: int) -> str | None:
    """判断是否需要浏览器重试，并返回可审计原因；附件类型不走浏览器。"""
    suffix = Path(urlparse(document.url).path).suffix.lower()
    if suffix in BROWSER_EXCLUDED_SUFFIXES:
        return None
    if document.http_status in BROWSER_RETRYABLE_STATUSES:
        return f"HTTP {document.http_status}"
    if document.media_type in {"text/html", "application/xhtml+xml"}:
        if looks_like_challenge_page(document.text):
            return "challenge-like HTML"
        if len(document.text.strip()) < min_content_chars:
            return f"main content shorter than {min_content_chars} characters"
    if document.error and document.media_type in {"unknown", "text/html", "application/xhtml+xml"}:
        return "HTTP transport failed"
    return None


def extract_html_main_content(data: bytes, base_url: str) -> tuple[str, list[tuple[str, str]]]:
    """抽取网页主正文，并按尺寸、替代文本和噪声规则筛选最多两张相关图片。"""

    html_text = _decode_text(data)
    extracted = trafilatura.extract(
        html_text,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        no_fallback=False,
    )
    soup = BeautifulSoup(html_text, "lxml")
    for tag in soup.find_all(BOILERPLATE_TAGS):
        tag.decompose()
    container = soup.find("main") or soup.find("article") or soup.find(attrs={"role": "main"})
    if not isinstance(container, Tag):
        container = soup.body if isinstance(soup.body, Tag) else soup
    fallback = container.get_text("\n", strip=True)
    text = _normalize_text(extracted or fallback)

    ranked_images: list[tuple[int, str, str]] = []
    for image in container.find_all("img"):
        src = image.get("data-src") or image.get("src")
        if not isinstance(src, str) or not src.strip():
            continue
        alt = " ".join(
            str(value) for value in (image.get("alt"), image.get("title"), image.get("class")) if value
        ).strip()
        absolute = urljoin(base_url, src.strip())
        if urlparse(absolute).scheme not in {"http", "https"}:
            continue
        noisy = bool(NOISY_IMAGE_PATTERN.search(absolute + " " + alt))
        width = int(image.get("width", 0)) if str(image.get("width", "")).isdigit() else 0
        height = int(image.get("height", 0)) if str(image.get("height", "")).isdigit() else 0
        if noisy or (width and width < 160) or (height and height < 100):
            continue
        score = (2 if alt else 0) + (1 if width >= 500 or height >= 300 else 0)
        ranked_images.append((score, absolute, alt))
    ranked_images.sort(key=lambda item: item[0], reverse=True)
    return text, [(url, alt) for _, url, alt in ranked_images[:MAX_IMAGES_PER_SOURCE]]


def extract_pdf(data: bytes, source_index: int) -> tuple[str, list[ImageEvidence]]:
    """提取限定页数的 PDF 文本，并渲染数字密度最高的代表页供视觉复核。"""

    document = pymupdf.open(stream=data, filetype="pdf")
    page_text: list[tuple[int, str]] = []
    for page_index in range(min(len(document), MAX_PDF_PAGES)):
        page_text.append((page_index, document[page_index].get_text("text")))
    text = _normalize_text("\n".join(value for _, value in page_text))

    # Render representative pages so charts and scanned text can participate in the same model call.
    ranked_pages = sorted(
        page_text,
        key=lambda item: (len(re.findall(r"\d", item[1])), len(item[1])),
        reverse=True,
    )
    if not ranked_pages and len(document):
        ranked_pages = [(0, "")]
    images: list[ImageEvidence] = []
    for page_index, _ in ranked_pages[:MAX_IMAGES_PER_SOURCE]:
        pixmap = document[page_index].get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4), alpha=False)
        images.append(
            ImageEvidence(
                label=f"Source {source_index}, PDF page {page_index + 1}",
                png=pixmap.tobytes("png"),
                source_index=source_index,
            )
        )
    document.close()
    return text, images


def extract_docx(data: bytes, source_index: int) -> tuple[str, list[ImageEvidence]]:
    """提取 DOCX 段落、表格与有限数量的内嵌图片。"""

    document = Document(io.BytesIO(data))
    lines = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
    for table in document.tables:
        for row in table.rows:
            lines.append("\t".join(cell.text.strip() for cell in row.cells))
    images: list[ImageEvidence] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        media_names = [name for name in archive.namelist() if name.startswith("word/media/")]
        for media_name in media_names[:MAX_IMAGES_PER_SOURCE]:
            converted = normalize_image(archive.read(media_name))
            if converted:
                images.append(
                    ImageEvidence(
                        label=f"Source {source_index}, Word image {Path(media_name).name}",
                        png=converted,
                        source_index=source_index,
                    )
                )
    return _normalize_text("\n".join(lines)), images


def extract_legacy_doc(data: bytes) -> str:
    """通过系统可用的 antiword/textutil 尽力读取旧版 DOC；失败返回空文本。"""

    with tempfile.NamedTemporaryFile(suffix=".doc") as source:
        source.write(data)
        source.flush()
        commands = [
            ["antiword", source.name],
            ["textutil", "-convert", "txt", "-stdout", source.name],
        ]
        for command in commands:
            try:
                completed = subprocess.run(command, capture_output=True, timeout=20, check=False)
            except FileNotFoundError, subprocess.TimeoutExpired:
                continue
            if completed.returncode == 0 and completed.stdout:
                return _normalize_text(_decode_text(completed.stdout))
    return ""


def normalize_image(data: bytes) -> bytes | None:
    """过滤小图，修正 EXIF 方向并缩放为模型可接受的 RGB PNG。"""

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.width < 160 or image.height < 100:
                return None
            converted = ImageOps.exif_transpose(image).convert("RGB")
            converted.thumbnail((1400, 1400))
            output = io.BytesIO()
            converted.save(output, format="PNG", optimize=True)
            return output.getvalue()
    except OSError, ValueError:
        return None


async def _download(
    client: httpx.AsyncClient,
    url: str,
    validate_url: Callable[[str], Awaitable[None]],
    *,
    max_bytes: int = MAX_DOCUMENT_BYTES,
) -> tuple[str, str, bytes]:
    """流式下载受限大小的公共资源，并再次校验重定向后的最终地址。"""

    await validate_url(url)
    async with client.stream("GET", url, headers={"User-Agent": "MetricPulse/1.0"}) as response:
        response.raise_for_status()
        final_url = str(response.url)
        await validate_url(final_url)
        declared = int(response.headers.get("content-length", 0) or 0)
        if declared > max_bytes:
            raise ValueError(f"source exceeds {max_bytes} bytes")
        content = bytearray()
        async for chunk in response.aiter_bytes():
            content.extend(chunk)
            if len(content) > max_bytes:
                raise ValueError(f"source exceeds {max_bytes} bytes")
    media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    return final_url, media_type, bytes(content)


async def _download_html_images(
    client: httpx.AsyncClient,
    references: list[tuple[str, str]],
    source_index: int,
    validate_url: Callable[[str], Awaitable[None]],
) -> list[ImageEvidence]:
    images: list[ImageEvidence] = []
    for url, alt in references:
        try:
            _, media_type, data = await _download(client, url, validate_url, max_bytes=6_000_000)
        except httpx.HTTPError, ValueError:
            continue
        if not media_type.startswith("image/"):
            continue
        converted = normalize_image(data)
        if converted:
            images.append(
                ImageEvidence(
                    label=f"Source {source_index}, {alt or 'web image'}",
                    png=converted,
                    source_index=source_index,
                )
            )
    return images


async def fetch_source_document(
    candidate: Any,
    index: int,
    client: httpx.AsyncClient,
    validate_url: Callable[[str], Awaitable[None]],
) -> SourceDocument:
    """按内容类型选择解析器，把所有可预期抓取失败收敛到 document.error。

    调用者可以据此决定浏览器降级或搜索降级，而无需用异常区分 HTTP、格式和解析错误。
    """

    document = SourceDocument(
        index=index,
        url=candidate.source_url,
        requested_url=candidate.source_url,
        title=candidate.title,
        snippet=candidate.excerpt,
        normalized_url=normalize_source_url(candidate.source_url),
    )
    try:
        final_url, media_type, data = await _download(client, document.url, validate_url)
        document.url = final_url
        document.media_type = media_type or "application/octet-stream"
        document.content_hash = hashlib.sha256(data).hexdigest()
        suffix = Path(urlparse(final_url).path).suffix.lower()
        if media_type in {"text/html", "application/xhtml+xml"} or suffix in {".html", ".htm", ""}:
            document.text, references = extract_html_main_content(data, final_url)
            document.images = await _download_html_images(client, references, index, validate_url)
            if looks_like_challenge_page(f"{final_url}\n{document.text}"):
                document.url = document.requested_url or document.url
                document.text = ""
                document.images = []
                document.error = "HTTP response is an access-challenge page"
        elif media_type == "application/pdf" or suffix == ".pdf" or data.startswith(b"%PDF"):
            document.text, document.images = extract_pdf(data, index)
        elif (
            media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or suffix == ".docx"
        ):
            document.text, document.images = extract_docx(data, index)
        elif media_type == "application/msword" or suffix == ".doc":
            document.text = extract_legacy_doc(data)
        elif media_type.startswith("image/"):
            converted = normalize_image(data)
            if converted:
                document.images = [ImageEvidence(f"Source {index}, image", converted, index)]
        elif media_type.startswith("text/") or suffix in {".csv", ".tsv", ".json", ".xml"}:
            document.text = _normalize_text(_decode_text(data))
        else:
            document.error = f"unsupported content type: {media_type or suffix or 'unknown'}"
    except httpx.HTTPStatusError as exc:
        document.http_status = exc.response.status_code
        retry_after = exc.response.headers.get("retry-after", "").strip()
        if retry_after.isdigit():
            document.retry_after_seconds = min(float(retry_after), 600)
        document.error = f"HTTP {exc.response.status_code}: {exc.response.reason_phrase}"
    except (httpx.HTTPError, ValueError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
        document.error = str(exc)
    return document


async def _render_document_in_browser(
    document: SourceDocument,
    context: Any,
    client: httpx.AsyncClient,
    validate_url: Callable[[str], Awaitable[None]],
    *,
    timeout_seconds: float,
    settle_seconds: float,
) -> None:
    """在隔离浏览器上下文中渲染单页，不处理真人验证挑战。

    导航请求仍经过 URL 安全校验；字体、媒体和 WebSocket 被阻止以降低成本。正文抽取后只
    截取主视口作为补充视觉证据，不把整页广告和导航截图送入模型。
    """

    page = await context.new_page()
    timeout_ms = max(1_000, int(timeout_seconds * 1_000))
    page.set_default_timeout(timeout_ms)
    try:
        response = await page.goto(
            document.requested_url or document.url,
            wait_until="domcontentloaded",
            timeout=timeout_ms,
        )
        with contextlib.suppress(Exception):  # optional on pages with long-lived requests
            await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 15_000))
        if settle_seconds > 0:
            await page.wait_for_timeout(settle_seconds * 1_000)

        final_url = page.url
        await validate_url(final_url)
        html_text = await page.content()
        text, references = extract_html_main_content(html_text.encode("utf-8"), final_url)
        title = await page.title()
        if looks_like_challenge_page(f"{final_url}\n{title}\n{text}") or looks_like_challenge_page(html_text):
            raise BrowserChallengeError("human-verification or access-challenge page detected")

        document.url = final_url
        document.title = document.title or title or None
        document.media_type = "text/html"
        document.text = text
        document.http_status = response.status if response is not None else None
        document.images = await _download_html_images(
            client,
            references,
            document.index,
            validate_url,
        )

        main = page.locator("main, article, [role='main']").first
        if await main.count():
            with contextlib.suppress(Exception):
                await main.scroll_into_view_if_needed(timeout=min(timeout_ms, 10_000))
        try:
            screenshot = await page.screenshot(
                full_page=False,
                animations="disabled",
                timeout=min(timeout_ms, 30_000),
            )
            converted = normalize_image(screenshot)
            if converted:
                document.images.insert(
                    0,
                    ImageEvidence(
                        label=f"Source {document.index}, browser-rendered main viewport",
                        png=converted,
                        source_index=document.index,
                    ),
                )
                document.images = document.images[:MAX_IMAGES_PER_SOURCE]
        except Exception:
            pass

        if not document.text and not document.images:
            raise RuntimeError("browser rendered no usable main content")
        document.browser_rendered = True
        document.error = None
    finally:
        await page.close()


async def apply_browser_fallbacks(
    documents: Sequence[SourceDocument],
    client: httpx.AsyncClient,
    validate_url: Callable[[str], Awaitable[None]],
    *,
    timeout_seconds: float,
    settle_seconds: float,
    min_content_chars: int,
    site_cooldown_seconds: float,
) -> None:
    """串行处理确有必要的浏览器降级，并按站点实施冷却时间。

    浏览器初始化失败不会终止整行采集，而是把同一错误写回所有待处理文档，供后续搜索或
    人工审核判断。
    """

    pending: list[SourceDocument] = []
    for document in documents:
        reason = browser_fallback_reason(document, min_content_chars=min_content_chars)
        if reason:
            document.browser_fallback_reason = reason
            pending.append(document)
    if not pending:
        return

    from playwright.async_api import async_playwright

    last_visit_started: dict[str, float] = {}
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            chromium_version = browser.version
            user_agent = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{chromium_version} Safari/537.36"
            )
            context = await browser.new_context(
                user_agent=user_agent,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1440, "height": 1000},
                color_scheme="light",
                service_workers="block",
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7"},
            )

            async def route_request(route: Any) -> None:
                request = route.request
                if request.resource_type in {"font", "media", "websocket"}:
                    await route.abort()
                    return
                if request.is_navigation_request():
                    try:
                        await validate_url(request.url)
                    except ValueError:
                        await route.abort()
                        return
                await route.continue_()

            await context.route("**/*", route_request)
            try:
                for document in pending:
                    hostname = urlparse(document.url).hostname or ""
                    earliest = last_visit_started.get(hostname, 0) + site_cooldown_seconds
                    delay = max(0.0, earliest - time.monotonic())
                    delay = max(delay, document.retry_after_seconds or 0)
                    if delay:
                        await asyncio.sleep(delay)
                    last_visit_started[hostname] = time.monotonic()
                    try:
                        await _render_document_in_browser(
                            document,
                            context,
                            client,
                            validate_url,
                            timeout_seconds=timeout_seconds,
                            settle_seconds=settle_seconds,
                        )
                    except BrowserChallengeError as exc:
                        document.url = document.requested_url or document.url
                        document.text = ""
                        document.images = []
                        document.error = f"browser fallback stopped: {exc}"
                    except Exception as exc:
                        document.url = document.requested_url or document.url
                        document.error = f"browser fallback failed: {exc}"
            finally:
                await context.close()
                await browser.close()
    except Exception as exc:
        for document in pending:
            if not document.browser_rendered:
                document.error = f"browser fallback unavailable: {exc}"


async def gather_source_documents(
    candidates: Sequence[Any],
    validate_url: Callable[[str], Awaitable[None]],
    *,
    concurrency: int = 5,
    browser_fallback_enabled: bool = False,
    browser_timeout_seconds: float = 180,
    browser_settle_seconds: float = 5,
    browser_min_content_chars: int = 500,
    browser_site_cooldown_seconds: float = 30,
) -> list[SourceDocument]:
    """并发获取候选来源，同时按 URL 合并同源请求并维护两级缓存。

    内存锁保证同一进程内相同 URL 只下载一次；持久缓存跨行、跨任务复用正文。浏览器降级
    在普通 HTTP 获取完成后统一执行，最终成功文本再原子写入持久缓存。
    """

    semaphore = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:

        async def guarded(candidate: Any, index: int) -> SourceDocument:
            cache_key = normalize_source_url(candidate.source_url)
            cached = _SOURCE_CACHE.get(cache_key)
            if cached is not None:
                return _cached_document(cached, candidate, index)
            lock = _SOURCE_CACHE_LOCKS.setdefault(cache_key, asyncio.Lock())
            async with lock:
                cached = _SOURCE_CACHE.get(cache_key)
                if cached is not None:
                    return _cached_document(cached, candidate, index)
                persistent = _load_persistent_document(cache_key, candidate, index)
                if persistent is not None:
                    if len(_SOURCE_CACHE) >= _SOURCE_CACHE_MAX_ITEMS:
                        _SOURCE_CACHE.pop(next(iter(_SOURCE_CACHE)))
                    _SOURCE_CACHE[cache_key] = copy.deepcopy(persistent)
                    return persistent
                async with semaphore:
                    normalized_candidate = copy.copy(candidate)
                    normalized_candidate.source_url = cache_key
                    document = await fetch_source_document(
                        normalized_candidate,
                        index,
                        client,
                        validate_url,
                    )
                    document.requested_url = candidate.source_url
                    document.normalized_url = cache_key
                if not document.error and (document.text or document.images):
                    if len(_SOURCE_CACHE) >= _SOURCE_CACHE_MAX_ITEMS:
                        _SOURCE_CACHE.pop(next(iter(_SOURCE_CACHE)))
                    _SOURCE_CACHE[cache_key] = copy.deepcopy(document)
                return document

        documents = list(
            await asyncio.gather(
                *(guarded(candidate, index) for index, candidate in enumerate(candidates, start=1))
            )
        )
        if browser_fallback_enabled:
            await apply_browser_fallbacks(
                documents,
                client,
                validate_url,
                timeout_seconds=browser_timeout_seconds,
                settle_seconds=browser_settle_seconds,
                min_content_chars=browser_min_content_chars,
                site_cooldown_seconds=browser_site_cooldown_seconds,
            )
        for document in documents:
            cache_key = document.normalized_url or normalize_source_url(
                document.requested_url or document.url
            )
            if not document.error and document.text:
                document.content_hash = (
                    document.content_hash or hashlib.sha256(document.text.encode()).hexdigest()
                )
                _SOURCE_CACHE[cache_key] = copy.deepcopy(document)
                _persist_document(cache_key, document)
        return documents


def build_contact_sheet(documents: Sequence[SourceDocument]) -> bytes | None:
    """按来源轮询选择图片拼成联系表，避免单一来源占满全部视觉槽位。"""

    images: list[ImageEvidence] = []
    for image_position in range(MAX_IMAGES_PER_SOURCE):
        for document in documents:
            if len(document.images) > image_position:
                images.append(document.images[image_position])
            if len(images) >= MAX_VISION_IMAGES:
                break
        if len(images) >= MAX_VISION_IMAGES:
            break
    if not images:
        return None
    cell_width, cell_height, label_height = 700, 520, 44
    columns = 2
    rows = (len(images) + columns - 1) // columns
    canvas = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(canvas)
    for index, evidence in enumerate(images):
        with Image.open(io.BytesIO(evidence.png)) as image:
            image = image.convert("RGB")
            image.thumbnail((cell_width - 20, cell_height - label_height - 20))
            x = (index % columns) * cell_width + (cell_width - image.width) // 2
            y = (index // columns) * cell_height + label_height
            canvas.paste(image, (x, y))
        label = evidence.label[:95]
        label_position = (
            (index % columns) * cell_width + 10,
            (index // columns) * cell_height + 10,
        )
        draw.text(label_position, label, fill="black")
    output = io.BytesIO()
    canvas.save(output, format="PNG", optimize=True)
    return output.getvalue()
