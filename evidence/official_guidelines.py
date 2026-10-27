"""Official guideline source registry and legally reusable retrieval."""

from __future__ import annotations

import hashlib
import html
import io
import re
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin

from pypdf import PdfReader

from evidence.external_retrievers import JsonGetTransport, UrllibJsonGetTransport, USER_AGENT


@dataclass(frozen=True)
class OfficialGuidelineSource:
    source_id: str
    display_name: str
    authority: str
    automation_status: str
    requirement: str
    search_url: str


@dataclass(frozen=True)
class OfficialGuidelineRecord:
    source: str
    resource_id: str
    title: str
    url: str
    year: int | None
    summary: str
    reference_number: str
    download_url: str | None = None
    full_text: str = ""
    publisher: str = ""
    authority_reason: str = "官方或专业机构指南"
    full_text_url: str | None = None
    publication_type: str = "Guideline"
    is_guideline_or_consensus: bool = True
    clinical_answer_eligible: bool = True


@dataclass
class OfficialGuidelinePacket:
    records: list[OfficialGuidelineRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class DocumentTextRetriever(Protocol):
    def fetch_text(self, url: str) -> str: ...


class PdfDocumentTextRetriever:
    """Read an official PDF transiently and return text without saving the file."""

    def __init__(self, *, max_cache_items: int = 4) -> None:
        self.max_cache_items = max(0, int(max_cache_items))
        self._cache: OrderedDict[str, str] = OrderedDict()

    def fetch_text(self, url: str) -> str:
        if url in self._cache:
            self._cache.move_to_end(url)
            return self._cache[url]
        text = self._download_and_extract(url)
        if self.max_cache_items and text:
            self._cache[url] = text
            self._cache.move_to_end(url)
            while len(self._cache) > self.max_cache_items:
                self._cache.popitem(last=False)
        return text

    def _download_and_extract(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/pdf"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=40.0) as response:
            payload = response.read(20 * 1024 * 1024 + 1)
        if len(payload) > 20 * 1024 * 1024:
            raise ValueError("官方指南 PDF 超过 20 MB 安全上限")
        reader = PdfReader(io.BytesIO(payload))
        text = "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
        text = re.sub(r"[ \t]+", " ", text).strip()
        if len(text) > 2_000_000:
            raise ValueError("官方指南提取文本超过 200 万字符安全上限")
        return text

    def clear_cache(self) -> None:
        self._cache.clear()


class _VisibleTextParser(HTMLParser):
    BLOCK_TAGS = {
        "article", "blockquote", "br", "dd", "div", "dl", "dt", "figcaption",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "p", "section", "table",
        "td", "th", "tr", "ul", "ol",
    }
    IGNORED_TAGS = {"script", "style", "svg", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.IGNORED_TAGS:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.IGNORED_TAGS and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth and data.strip():
            self.parts.append(data)


class HtmlDocumentTextRetriever:
    """Read a public HTML full-text page without executing page scripts."""

    def fetch_text(self, url: str) -> str:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=40.0) as response:
            payload = response.read(5 * 1024 * 1024 + 1)
        if len(payload) > 5 * 1024 * 1024:
            raise ValueError("官方开放全文 HTML 超过 5 MB 安全上限")
        parser = _VisibleTextParser()
        parser.feed(payload.decode("utf-8", errors="replace"))
        text = " ".join(parser.parts)
        text = re.sub(r"[ \t\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()
        if len(text) > 2_000_000:
            raise ValueError("官方开放全文提取文本超过 200 万字符安全上限")
        return text


class WhoOfficialGuidelineRetriever:
    """Search WHO's public publication API and transiently read open PDFs."""

    BASE = "https://www.who.int/api/hubs/publications"
    GUIDELINE_MARKERS = ("guideline", "guidance", "recommendation")

    def __init__(
        self,
        transport: JsonGetTransport | None = None,
        document_retriever: DocumentTextRetriever | None = None,
    ) -> None:
        self.transport = transport or UrllibJsonGetTransport()
        self.document_retriever = document_retriever or PdfDocumentTextRetriever()

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        full_text_limit: int = 2,
    ) -> OfficialGuidelinePacket:
        terms = _english_terms(query)[:2]
        if not terms:
            return OfficialGuidelinePacket(
                warnings=["WHO 指南检索缺少可用英文主题词"]
            )
        topic_filters = [
            f"(contains(tolower(Title),'{_odata_literal(term)}') or "
            f"contains(tolower(Summary),'{_odata_literal(term)}'))"
            for term in terms
        ]
        kind_filter = "(" + " or ".join(
            f"contains(tolower(Title),'{marker}')"
            for marker in self.GUIDELINE_MARKERS
        ) + ")"
        data = self.transport.get_json(
            self.BASE,
            {
                "$filter": f"({' or '.join(topic_filters)}) and {kind_filter}",
                "$top": max(1, min(limit * 2, 20)),
                "$orderby": "PublicationDate desc",
                "$select": (
                    "Title,Summary,PublicationDate,ItemDefaultUrl,DownloadUrl,"
                    "WHOReferenceNumber"
                ),
            },
            {"User-Agent": USER_AGENT, "Accept": "application/json"},
            30.0,
        )
        packet = OfficialGuidelinePacket()
        download_attempts = 0
        ranked_items = []
        for item in data.get("value", []):
            title = _clean_html(item.get("Title"))
            if not title or not any(marker in title.lower() for marker in self.GUIDELINE_MARKERS):
                continue
            searchable = f"{title} {_clean_html(item.get('Summary'))}".lower()
            relevance = sum(term in searchable for term in terms)
            ranked_items.append((relevance, item, title))
        ranked_items.sort(
            key=lambda entry: (entry[0], str(entry[1].get("PublicationDate") or "")),
            reverse=True,
        )
        for _, item, title in ranked_items:
            item_url = str(item.get("ItemDefaultUrl") or "").strip()
            url = urljoin("https://www.who.int", item_url) if item_url else ""
            download_url = str(item.get("DownloadUrl") or "").strip() or None
            reference = str(item.get("WHOReferenceNumber") or "").strip()
            resource_id = reference or item_url.strip("/") or hashlib.sha256(
                title.encode("utf-8")
            ).hexdigest()[:16]
            full_text = ""
            if download_url and download_attempts < max(0, full_text_limit):
                download_attempts += 1
                try:
                    full_text = self.document_retriever.fetch_text(download_url)
                    if not full_text:
                        packet.warnings.append(f"WHO {resource_id} PDF 未提取到文本")
                except Exception as exc:
                    packet.warnings.append(
                        f"WHO {resource_id} 全文获取失败: {type(exc).__name__}"
                    )
            packet.records.append(OfficialGuidelineRecord(
                source="who",
                resource_id=resource_id,
                title=title,
                url=url or download_url or "https://www.who.int/publications",
                year=_year(item.get("PublicationDate")),
                summary=_clean_html(item.get("Summary"))[:2000],
                reference_number=reference,
                download_url=download_url,
                full_text=full_text,
                publisher="World Health Organization",
                authority_reason="国际官方机构指南",
            ))
            if len(packet.records) >= limit:
                break
        return packet


class CdcHypoglycemiaGuidanceRetriever:
    """Retrieve the CDC's public hypoglycemia first-aid guidance."""

    ARTICLE_URL = (
        "https://www.cdc.gov/diabetes/treatment/"
        "treatment-low-blood-sugar-hypoglycemia.html"
    )
    TITLE = "Treatment of Low Blood Sugar (Hypoglycemia)"
    HYPOGLYCEMIA_MARKERS = (
        "hypoglycemia", "hypoglycaemia", "low blood glucose", "low blood sugar",
        "低血糖",
    )

    def __init__(
        self,
        document_retriever: DocumentTextRetriever | None = None,
    ) -> None:
        self.document_retriever = document_retriever or HtmlDocumentTextRetriever()

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        full_text_limit: int = 1,
    ) -> OfficialGuidelinePacket:
        lowered = str(query).lower()
        if not any(marker in lowered for marker in self.HYPOGLYCEMIA_MARKERS):
            return OfficialGuidelinePacket()

        packet = OfficialGuidelinePacket()
        full_text = ""
        full_text_url = None
        if limit > 0 and full_text_limit > 0:
            try:
                full_text = self.document_retriever.fetch_text(self.ARTICLE_URL)
                full_text = self._article_body(full_text)
                full_text_url = self.ARTICLE_URL if full_text else None
                if not full_text:
                    packet.warnings.append("CDC 低血糖处置页面未提取到文本")
            except Exception as exc:
                packet.warnings.append(
                    f"CDC 低血糖处置正文获取失败: {type(exc).__name__}"
                )
        packet.records.append(OfficialGuidelineRecord(
            source="cdc",
            resource_id="CDC-HYPOGLYCEMIA-TREATMENT-2024",
            title=self.TITLE,
            url=self.ARTICLE_URL,
            year=2024,
            summary=(
                "美国疾病控制与预防中心的低血糖处置指引，涵盖 15-15 法则、"
                "可快速吸收的糖类、严重低血糖时的胰高血糖素与紧急就医。"
            ),
            reference_number="CDC Diabetes, May 15, 2024",
            full_text=full_text,
            publisher="Centers for Disease Control and Prevention",
            authority_reason="国家公共卫生机构官方处置指引",
            full_text_url=full_text_url,
            publication_type="Official health guidance",
            is_guideline_or_consensus=False,
            clinical_answer_eligible=True,
        ))
        return packet

    @classmethod
    def _article_body(cls, text: str) -> str:
        """Remove repeated CDC navigation while retaining the article wording."""
        if not text:
            return ""
        starts = [match.start() for match in re.finditer(re.escape(cls.TITLE), text)]
        start = starts[1] if len(starts) > 1 else (starts[0] if starts else 0)
        end = text.find("Content Source:", start)
        if end < 0:
            end = len(text)
        return text[start:end].strip()


class OfficialGuidelineRetriever:
    """Composite entry point with per-source failure isolation."""

    def __init__(
        self,
        who: WhoOfficialGuidelineRetriever | None = None,
        cdc: CdcHypoglycemiaGuidanceRetriever | None = None,
    ) -> None:
        self.who = who or WhoOfficialGuidelineRetriever()
        self.cdc = cdc or CdcHypoglycemiaGuidanceRetriever()

    def search(self, query: str, *, limit: int = 5) -> OfficialGuidelinePacket:
        combined = OfficialGuidelinePacket()
        remaining = max(1, int(limit))
        for name, retriever in (("CDC", self.cdc), ("WHO", self.who)):
            if remaining <= 0:
                break
            try:
                packet = retriever.search(query, limit=remaining)
                combined.records.extend(packet.records)
                combined.warnings.extend(packet.warnings)
                remaining = max(0, limit - len(combined.records))
            except Exception as exc:
                combined.warnings.append(
                    f"{name} 官方指南检索失败: {type(exc).__name__}"
                )
        return combined


def official_guideline_source_options() -> tuple[OfficialGuidelineSource, ...]:
    return (
        OfficialGuidelineSource(
            "who",
            "世界卫生组织 WHO",
            "国际官方机构",
            "automatic",
            "公开出版物 API；开放 PDF 仅瞬时解析",
            "https://www.who.int/publications",
        ),
        OfficialGuidelineSource(
            "cdc",
            "美国疾病控制与预防中心 CDC",
            "国家公共卫生机构",
            "automatic",
            "公开健康指引网页；当前接入低血糖处置主题",
            CdcHypoglycemiaGuidanceRetriever.ARTICLE_URL,
        ),
        OfficialGuidelineSource(
            "ada",
            "美国糖尿病学会 ADA",
            "国际专业学会",
            "official_search_only",
            "可定位 Standards of Care，但自动处理全文需另行取得许可",
            (
                "https://diabetesjournals.org/care/article/49/Supplement_1/S132/"
                "163927/6-Glycemic-Goals-Hypoglycemia-and-Hyperglycemic"
            ),
        ),
        OfficialGuidelineSource(
            "nice",
            "英国 NICE",
            "国家级指南机构",
            "license_required",
            "正式 Syndication API 需向 NICE 申请许可",
            "https://www.nice.org.uk/guidance/published",
        ),
        OfficialGuidelineSource(
            "nhc_cn",
            "中国国家卫生健康委员会",
            "中国国家卫生行政机构",
            "official_search_only",
            "官网无稳定公开 API，禁止绕过反自动化校验",
            "https://www.nhc.gov.cn/cms-search/xxgk/searchList.htm?serverType=&type=search",
        ),
    )


def _english_terms(value: str) -> list[str]:
    stop = {
        "and", "or", "the", "of", "for", "in", "with", "management",
        "treatment", "guideline", "guidelines", "recommendation", "recommendations",
    }
    terms = re.findall(r"[a-z][a-z0-9-]{2,}", str(value).lower())
    return list(dict.fromkeys(term for term in terms if term not in stop))


def _odata_literal(value: str) -> str:
    return value.replace("'", "''")


def _clean_html(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _year(value: object) -> int | None:
    match = re.search(r"\b(19|20)\d{2}\b", str(value or ""))
    return int(match.group(0)) if match else None
