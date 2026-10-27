"""Traceable clients for Wikipedia, PubMed, and OpenAlex metadata retrieval."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


USER_AGENT = "MedScholar-Medical-Learning-Companion/0.2 (educational retrieval client)"


class JsonGetTransport(Protocol):
    def get_json(
        self,
        url: str,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


class TextGetTransport(Protocol):
    def get_text(
        self,
        url: str,
        params: Mapping[str, Any],
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> str: ...


class UrllibJsonGetTransport:
    def get_json(self, url, params, headers, timeout_seconds):
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}", headers=dict(headers), method="GET"
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(8 * 1024 * 1024)
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("外部检索响应必须是JSON对象")
        return data


class UrllibTextGetTransport:
    def get_text(self, url, params, headers, timeout_seconds):
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}", headers=dict(headers), method="GET"
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read(16 * 1024 * 1024).decode("utf-8")


@dataclass(frozen=True)
class ExternalResource:
    source: str
    resource_id: str
    title: str
    url: str
    snippet: str = ""
    year: int | None = None
    authors: tuple[str, ...] = field(default_factory=tuple)
    identifiers: Mapping[str, str] = field(default_factory=dict)
    publication_types: tuple[str, ...] = field(default_factory=tuple)
    cited_by_count: int = 0


@dataclass(frozen=True)
class OpenFullTextRecord:
    pmcid: str
    title: str
    url: str
    license_text: str
    full_text: str


class PmcOpenAccessRetriever:
    """Retrieve reusable JATS full text through PMC's official OAI-PMH API."""

    BASE = "https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/"

    def __init__(self, transport: TextGetTransport | None = None) -> None:
        self.transport = transport or UrllibTextGetTransport()

    def fetch(self, pmcid: str) -> OpenFullTextRecord | None:
        normalized = str(pmcid).strip().upper().removeprefix("PMC")
        if not normalized.isdigit():
            raise ValueError("PMCID 格式无效")
        xml_text = self.transport.get_text(
            self.BASE,
            {
                "verb": "GetRecord",
                "identifier": f"oai:pubmedcentral.nih.gov:{normalized}",
                "metadataPrefix": "pmc",
            },
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/xml",
            },
            30.0,
        )
        root = ET.fromstring(xml_text)
        if any(_local_name(node.tag) == "error" for node in root.iter()):
            return None
        article = next(
            (node for node in root.iter() if _local_name(node.tag) == "article"),
            None,
        )
        if article is None:
            return None
        title_node = next(
            (node for node in article.iter() if _local_name(node.tag) == "article-title"),
            None,
        )
        title = _node_text(title_node)
        license_node = next(
            (node for node in article.iter() if _local_name(node.tag) == "license"),
            None,
        )
        license_text = _node_text(license_node)
        body = next(
            (node for node in article.iter() if _local_name(node.tag) == "body"),
            None,
        )
        if body is None:
            return None
        sections = []
        for section in [node for node in body.iter() if _local_name(node.tag) == "sec"]:
            heading_node = next(
                (child for child in section if _local_name(child.tag) == "title"),
                None,
            )
            paragraphs = [
                _node_text(node)
                for node in section
                if _local_name(node.tag) == "p" and _node_text(node)
            ]
            if paragraphs:
                heading = _node_text(heading_node)
                sections.append("\n".join(([heading] if heading else []) + paragraphs))
        full_text = "\n\n".join(sections).strip()
        if not full_text:
            full_text = _node_text(body)
        if not full_text:
            return None
        return OpenFullTextRecord(
            pmcid=f"PMC{normalized}",
            title=title,
            url=f"https://pmc.ncbi.nlm.nih.gov/articles/PMC{normalized}/",
            license_text=license_text,
            full_text=full_text[:500_000],
        )


class WikipediaRetriever:
    def __init__(self, transport: JsonGetTransport | None = None) -> None:
        self.transport = transport or UrllibJsonGetTransport()

    def search(self, query: str, *, language: str = "zh", limit: int = 3):
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        data = self.transport.get_json(
            endpoint,
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrlimit": max(1, min(limit, 5)),
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "format": "json",
                "formatversion": 2,
            },
            {"User-Agent": USER_AGENT, "Accept": "application/json"},
            15.0,
        )
        pages = data.get("query", {}).get("pages", [])
        return tuple(
            ExternalResource(
                source=f"wikipedia_{language}",
                resource_id=str(page.get("pageid", page.get("title", ""))),
                title=str(page.get("title", "")),
                url=str(page.get("fullurl", "")),
                snippet=str(page.get("extract", ""))[:1200],
            )
            for page in pages
            if page.get("title") and page.get("fullurl")
        )


class PubMedRetriever:
    BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(
        self,
        transport: JsonGetTransport | None = None,
        text_transport: TextGetTransport | None = None,
        *,
        api_key: str | None = None,
        email: str | None = None,
        tool: str = "smu_physiology_tutor",
    ) -> None:
        self.transport = transport or UrllibJsonGetTransport()
        self.text_transport = text_transport or UrllibTextGetTransport()
        self.api_key = api_key or os.environ.get("NCBI_API_KEY")
        self.email = email or os.environ.get("NCBI_EMAIL")
        self.tool = tool

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        year_from: int | None = None,
        year_to: int | None = None,
        include_abstracts: bool = False,
    ):
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        effective_query = query
        if year_from or year_to:
            start = year_from or 1800
            end = year_to or 3000
            effective_query = f"({query}) AND ({start}[pdat] : {end}[pdat])"
        search = self.transport.get_json(
            f"{self.BASE}/esearch.fcgi",
            self._params({
                "db": "pubmed",
                "term": effective_query,
                "retmode": "json",
                "retmax": max(1, min(limit, 10)),
                "sort": "relevance",
            }),
            headers,
            20.0,
        )
        ids = [str(item) for item in search.get("esearchresult", {}).get("idlist", [])]
        if not ids:
            return ()
        summary = self.transport.get_json(
            f"{self.BASE}/esummary.fcgi",
            self._params({"db": "pubmed", "id": ",".join(ids), "retmode": "json"}),
            headers,
            20.0,
        )
        result = summary.get("result", {})
        abstracts = (
            self._fetch_abstracts(ids, headers) if include_abstracts else {}
        )
        resources = []
        for pmid in ids:
            item = result.get(pmid, {})
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            article_ids = {
                str(entry.get("idtype")): str(entry.get("value"))
                for entry in item.get("articleids", [])
                if entry.get("idtype") and entry.get("value")
            }
            resources.append(
                ExternalResource(
                    source="pubmed",
                    resource_id=pmid,
                    title=title,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    snippet=abstracts.get(pmid) or str(item.get("fulljournalname", "")),
                    year=_first_year(str(item.get("pubdate", ""))),
                    authors=tuple(
                        str(author.get("name"))
                        for author in item.get("authors", [])
                        if author.get("name")
                    ),
                    identifiers=article_ids,
                    publication_types=tuple(
                        str(value) for value in item.get("pubtype", []) if value
                    ),
                )
            )
        return tuple(resources)

    def _fetch_abstracts(self, ids: list[str], headers: Mapping[str, str]):
        xml_text = self.text_transport.get_text(
            f"{self.BASE}/efetch.fcgi",
            self._params({
                "db": "pubmed",
                "id": ",".join(ids),
                "rettype": "abstract",
                "retmode": "xml",
            }),
            headers,
            25.0,
        )
        root = ET.fromstring(xml_text)
        abstracts: dict[str, str] = {}
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", default="").strip()
            sections = []
            for node in article.findall(".//Abstract/AbstractText"):
                text = "".join(node.itertext()).strip()
                label = node.attrib.get("Label")
                if text:
                    sections.append(f"{label}: {text}" if label else text)
            if pmid and sections:
                abstracts[pmid] = "\n".join(sections)
        return abstracts

    def _params(self, values: Mapping[str, Any]) -> dict[str, Any]:
        params = dict(values)
        params["tool"] = self.tool
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        return params


class OpenAlexRetriever:
    def __init__(
        self,
        transport: JsonGetTransport | None = None,
        *,
        api_key: str | None = None,
    ) -> None:
        self.transport = transport or UrllibJsonGetTransport()
        self.api_key = api_key or os.environ.get("OPENALEX_API_KEY")

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        year_from: int | None = None,
        year_to: int | None = None,
    ):
        params: dict[str, Any] = {
            "search": query,
            "per_page": max(1, min(limit, 10)),
            "sort": "relevance_score:desc",
            "select": "id,display_name,publication_year,doi,authorships,primary_location,abstract_inverted_index,type,cited_by_count",
        }
        filters = []
        if year_from:
            filters.append(f"from_publication_date:{year_from}-01-01")
        if year_to:
            filters.append(f"to_publication_date:{year_to}-12-31")
        if filters:
            params["filter"] = ",".join(filters)
        if self.api_key:
            params["api_key"] = self.api_key
        data = self.transport.get_json(
            "https://api.openalex.org/works",
            params,
            {"User-Agent": USER_AGENT, "Accept": "application/json"},
            20.0,
        )
        resources = []
        for item in data.get("results", []):
            openalex_id = str(item.get("id", ""))
            title = str(item.get("display_name", "")).strip()
            if not openalex_id or not title:
                continue
            doi = str(item.get("doi") or "")
            location = item.get("primary_location") or {}
            landing = str(location.get("landing_page_url") or doi or openalex_id)
            resources.append(
                ExternalResource(
                    source="openalex",
                    resource_id=openalex_id.rsplit("/", 1)[-1],
                    title=title,
                    url=landing,
                    snippet=_abstract_from_inverted(item.get("abstract_inverted_index"))[:1200],
                    year=_optional_int(item.get("publication_year")),
                    authors=tuple(
                        str(authorship.get("author", {}).get("display_name"))
                        for authorship in item.get("authorships", [])[:8]
                        if authorship.get("author", {}).get("display_name")
                    ),
                    identifiers={"doi": doi} if doi else {},
                    publication_types=(str(item.get("type")),) if item.get("type") else (),
                    cited_by_count=_optional_int(item.get("cited_by_count")) or 0,
                )
            )
        return tuple(resources)


def _abstract_from_inverted(value: Any) -> str:
    if not isinstance(value, Mapping):
        return ""
    positioned = []
    for word, positions in value.items():
        if isinstance(positions, list):
            positioned.extend((int(position), str(word)) for position in positions)
    return " ".join(word for _, word in sorted(positioned))


def _local_name(tag: str) -> str:
    """Return an XML element name without its namespace prefix."""

    return str(tag).rsplit("}", 1)[-1]


def _node_text(node: ET.Element | None) -> str:
    """Flatten mixed-content JATS XML into normalized readable text."""

    if node is None:
        return ""
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def _first_year(value: str) -> int | None:
    for token in value.replace("-", " ").split():
        if len(token) == 4 and token.isdigit():
            return int(token)
    return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
