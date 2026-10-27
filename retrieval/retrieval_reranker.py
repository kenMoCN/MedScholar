"""Model-assisted relevance reranking for already retrieved local evidence."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping, Protocol

from retrieval.structure_retriever import RetrievalResult


class JsonClient(Protocol):
    def complete_json(
        self, system_instruction: str, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]: ...


class RetrievalReranker:
    def __init__(self, client: JsonClient) -> None:
        self.client = client

    def rerank(
        self,
        question: str,
        result: RetrievalResult,
        *,
        max_hits: int = 6,
    ) -> RetrievalResult:
        if len(result.hits) <= 2:
            return result
        candidates = [
            {
                "candidate_id": f"c{index}",
                "source": hit.original_name,
                "locator": hit.locator,
                "section": hit.section_title,
                "text": (hit.snippet or hit.context_text)[:1400],
            }
            for index, hit in enumerate(result.hits)
        ]
        try:
            raw = self.client.complete_json(
                (
                    "你是医学课程证据候选重排器。只判断候选页面能否直接帮助回答"
                    "问题，不回答问题本身。网页工具栏、装饰图、仅共享关键词但讨论"
                    "不同机制的页面应标为噪声。只输出JSON：ranking数组，每项包含"
                    "candidate_id、coverage(0到1)、noise(boolean)、reason。"
                ),
                {"question": question, "candidates": candidates},
            )
        except Exception:
            return result
        rows = raw.get("ranking", [])
        if not isinstance(rows, list):
            return result
        scores: dict[str, tuple[float, bool]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            candidate_id = str(row.get("candidate_id", ""))
            try:
                coverage = max(0.0, min(float(row.get("coverage", 0.0)), 1.0))
            except (TypeError, ValueError):
                continue
            scores[candidate_id] = (coverage, bool(row.get("noise", False)))
        ranked = []
        for index, hit in enumerate(result.hits):
            coverage, noise = scores.get(f"c{index}", (0.0, False))
            if noise or coverage < 0.25:
                continue
            ranked.append((coverage, hit.score, replace(hit, score=hit.score + coverage)))
        if not ranked:
            return result
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return RetrievalResult(
            query=result.query,
            route_mode=f"{result.route_mode}+reranked",
            routed_documents=result.routed_documents,
            hits=[item[2] for item in ranked[:max_hits]],
        )
