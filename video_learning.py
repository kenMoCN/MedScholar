"""Traceable video-resource search with honest platform-search fallback."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class VideoLearningResource:
    resource_id: str
    title: str
    url: str
    platform: str
    description: str
    resource_kind: str = "video_candidate"
    search_keyword: str = ""
    recommendation_reason: str = ""


@dataclass(frozen=True)
class VideoSearchPlan:
    concept: str
    search_queries: tuple[str, ...]
    selection_focus: str


class TextFetcher(Protocol):
    def get_text(self, url: str, *, timeout_seconds: float = 8.0) -> str: ...


class JsonClient(Protocol):
    def complete_json(
        self, system_instruction: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class UrlTextFetcher:
    def get_text(self, url: str, *, timeout_seconds: float = 8.0) -> str:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "MedScholar-LearningCompanion/1.0",
                "Accept": "application/json, application/rss+xml, application/xml, text/xml",
                "Referer": "https://search.bilibili.com/",
            },
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")


class VideoLearningRetriever:
    """Plan focused searches, retrieve videos, then select teaching candidates."""

    def __init__(
        self,
        fetcher: TextFetcher | None = None,
        *,
        planner: JsonClient | None = None,
        selector: JsonClient | None = None,
    ) -> None:
        self.fetcher = fetcher or UrlTextFetcher()
        self.planner = planner
        self.selector = selector

    def search(self, topic: str, *, max_results: int = 3) -> list[VideoLearningResource]:
        topic = str(topic or "").strip()
        if not topic:
            return []
        plan = self._plan(topic)
        candidates: list[VideoLearningResource] = []
        seen_urls: set[str] = set()
        for query_index, keyword in enumerate(plan.search_queries, start=1):
            api_url = (
                "https://api.bilibili.com/x/web-interface/search/type"
                "?search_type=video&keyword=" + urllib.parse.quote(keyword)
            )
            try:
                api_text = self.fetcher.get_text(api_url)
                found = self._parse_bilibili_json(
                    api_text,
                    plan.concept,
                    search_keyword=keyword,
                    id_prefix=f"video_{query_index}_bili",
                )
            except Exception:
                found = []
            if not found:
                web_query = f'site:bilibili.com/video 医学 {keyword}'
                rss_url = (
                    "https://cn.bing.com/search?format=rss&q="
                    + urllib.parse.quote(web_query)
                )
                try:
                    xml_text = self.fetcher.get_text(rss_url)
                    found = self._parse_rss(
                        xml_text,
                        plan.concept,
                        search_keyword=keyword,
                        id_prefix=f"video_{query_index}",
                    )
                except Exception:
                    found = []
            for item in found:
                if item.url in seen_urls:
                    continue
                seen_urls.add(item.url)
                candidates.append(item)
        if candidates:
            return self._select(topic, plan, candidates, max_results=max_results)
        return self._fallback_search_entries(plan.search_queries)

    @staticmethod
    def _parse_bilibili_json(
        json_text: str,
        topic: str,
        *,
        search_keyword: str,
        id_prefix: str,
    ) -> list[VideoLearningResource]:
        raw = json.loads(json_text)
        if raw.get("code") != 0:
            return []
        records = (raw.get("data") or {}).get("result") or []
        if not isinstance(records, list):
            return []
        scored = []
        seen = set()
        for index, record in enumerate(records[:20], start=1):
            if not isinstance(record, Mapping):
                continue
            bvid = str(record.get("bvid", "")).strip()
            title = _plain_title(str(record.get("title", "")))
            description = _plain_text(str(record.get("description", "")))
            if not re.fullmatch(r"BV[0-9A-Za-z]+", bvid) or not title:
                continue
            url = f"https://www.bilibili.com/video/{bvid}/"
            if url in seen:
                continue
            seen.add(url)
            author = _plain_text(str(record.get("author", "")))
            duration = _plain_text(str(record.get("duration", "")))
            metadata = " · ".join(
                item for item in (f"UP主：{author}" if author else "", f"时长：{duration}" if duration else "")
                if item
            )
            summary = description[:180] or "Bilibili公开搜索结果，打开后可查看完整讲解。"
            if metadata:
                summary = f"{metadata}\n{summary}"
            score = _topic_overlap(topic, f"{title} {description} {search_keyword}")
            scored.append((score, index, VideoLearningResource(
                resource_id=f"{id_prefix}_{index}",
                title=title,
                url=url,
                platform="Bilibili",
                description=summary,
                search_keyword=search_keyword,
            )))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored if item[0] > 0]

    def _plan(self, topic: str) -> VideoSearchPlan:
        fallback = VideoSearchPlan(
            concept=_compact_topic(topic),
            search_queries=(_compact_topic(topic),),
            selection_focus="优先选择能直接解释核心机制、适合医学生学习的视频",
        )
        if self.planner is None:
            return fallback
        try:
            raw = self.planner.complete_json(
                (
                    "你是医学学习视频检索规划器。根据学生当前问题提炼真正要学习的知识点，"
                    "生成1到3个适合视频平台检索的短关键词组合。不要照抄用户整句话，"
                    "不要包含‘帮我找视频’‘为什么’等对检索无帮助的表达。关键词应包含"
                    "医学概念以及必要的教学形式，例如机制、动画、图解或实验。只输出JSON。"
                ),
                {
                    "question": topic,
                    "output_schema": {
                        "concept": "核心知识点，简短中文",
                        "search_queries": ["2到12个词组成的检索关键词"],
                        "selection_focus": "筛选视频时最应关注的教学目标",
                    },
                },
            )
            concept = str(raw.get("concept", "")).strip()
            focus = str(raw.get("selection_focus", "")).strip()
            queries = _clean_search_queries(raw.get("search_queries", []))
            if concept and queries:
                return VideoSearchPlan(
                    concept=concept[:80],
                    search_queries=queries,
                    selection_focus=focus[:160] or fallback.selection_focus,
                )
        except Exception:
            pass
        return fallback

    def _select(
        self,
        topic: str,
        plan: VideoSearchPlan,
        candidates: list[VideoLearningResource],
        *,
        max_results: int,
    ) -> list[VideoLearningResource]:
        ranked = sorted(
            candidates,
            key=lambda item: -_topic_overlap(
                " ".join((plan.concept, *plan.search_queries)),
                f"{item.title} {item.description}",
            ),
        )
        if self.selector is None:
            return ranked[:max_results]
        try:
            raw = self.selector.complete_json(
                (
                    "你是医学生的视频学习资源筛选器。只能从给定候选中选择1到3个最相关视频。"
                    "依据题目匹配度、是否直接讲清核心机制、是否像完整教学内容进行筛选。"
                    "搜索摘要不能作为医学事实证据，也不要虚构播放量、作者资质或视频内容。"
                    "如果候选明显不相关，返回空列表。只输出JSON。"
                ),
                {
                    "student_question": topic,
                    "learning_concept": plan.concept,
                    "selection_focus": plan.selection_focus,
                    "candidates": [
                        {
                            "resource_id": item.resource_id,
                            "title": item.title,
                            "snippet": item.description,
                            "search_keyword": item.search_keyword,
                        }
                        for item in ranked[:12]
                    ],
                    "output_schema": {
                        "selected": [
                            {
                                "resource_id": "候选ID",
                                "reason": "为什么适合当前问题",
                            }
                        ]
                    },
                },
            )
            selected = raw.get("selected", [])
            by_id = {item.resource_id: item for item in ranked}
            output = []
            used = set()
            if isinstance(selected, list):
                for choice in selected:
                    if not isinstance(choice, Mapping):
                        continue
                    resource_id = str(choice.get("resource_id", "")).strip()
                    reason = str(choice.get("reason", "")).strip()
                    item = by_id.get(resource_id)
                    if item is None or resource_id in used:
                        continue
                    used.add(resource_id)
                    output.append(replace(
                        item,
                        description=(
                            f"推荐理由：{reason[:140]}\n{item.description}"
                            if reason else item.description
                        ),
                        recommendation_reason=reason[:140],
                    ))
                    if len(output) >= max_results:
                        break
            if output:
                return output
        except Exception:
            pass
        return ranked[:max_results]

    @staticmethod
    def _parse_rss(
        xml_text: str,
        topic: str,
        *,
        search_keyword: str = "",
        id_prefix: str = "video",
    ) -> list[VideoLearningResource]:
        root = ET.fromstring(xml_text)
        scored = []
        seen = set()
        for index, item in enumerate(root.findall(".//item"), start=1):
            title = _plain_title(item.findtext("title", ""))
            url = str(item.findtext("link", "")).strip()
            description = _plain_text(item.findtext("description", ""))
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                continue
            host = parsed.netloc.lower()
            if "bilibili.com" not in host or "/video/" not in parsed.path:
                continue
            canonical = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if canonical in seen or not title:
                continue
            seen.add(canonical)
            score = _topic_overlap(topic, f"{title} {description}")
            scored.append((score, index, VideoLearningResource(
                resource_id=f"{id_prefix}_{index}",
                title=title,
                url=canonical,
                platform="Bilibili",
                description=(
                    description[:180]
                    or "来自公开搜索结果的视频候选，请打开后核对讲解范围与课程版本。"
                ),
                search_keyword=search_keyword,
            )))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored if item[0] > 0]

    @staticmethod
    def _fallback_search_entries(
        search_queries: tuple[str, ...],
    ) -> list[VideoLearningResource]:
        output = []
        for index, query in enumerate(search_queries[:3], start=1):
            keyword = urllib.parse.quote(query)
            output.append(VideoLearningResource(
                resource_id=f"bilibili_search_{index}",
                title=f"在 Bilibili 搜索：{query}",
                url=f"https://search.bilibili.com/all?keyword={keyword}",
                platform="Bilibili",
                description=(
                    "已由模型提炼检索关键词，但暂时没有获得可核对的具体视频候选；"
                    "这是平台搜索入口，并非已精选视频。"
                ),
                resource_kind="platform_search",
                search_keyword=query,
            ))
        return output


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(value))).strip()


def _plain_title(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html.unescape(value))).strip()


def _topic_overlap(topic: str, candidate: str) -> int:
    compact_topic = re.sub(r"\s+", "", topic.lower())
    compact_candidate = re.sub(r"\s+", "", candidate.lower())
    terms = {
        compact_topic[index:index + 2]
        for index in range(max(0, len(compact_topic) - 1))
        if not re.fullmatch(r"[，。！？、；：,.!?;:]", compact_topic[index:index + 2])
    }
    return sum(term in compact_candidate for term in terms)


def _compact_topic(topic: str) -> str:
    cleaned = re.sub(
        r"(请|可以|能不能|能否|帮我|给我|有没有|找一下|推荐|适合|看的|视频|b站|哔哩哔哩)",
        " ",
        str(topic).lower(),
    )
    cleaned = re.sub(r"[？?！!。,.，：:；;]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:80] or str(topic).strip()[:80]


def _clean_search_queries(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    output = []
    for item in value:
        query = re.sub(r"\s+", " ", str(item)).strip(" ，。；;：:")
        if not query or len(query) > 80:
            continue
        if query not in output:
            output.append(query)
        if len(output) >= 3:
            break
    return tuple(output)
