"""Deterministic evidence integrity and claim-binding verification."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from evidence.evidence_bundle import EvidenceBundle, EvidenceItem
from retrieval.page_text_index import PageTextIndex, load_page_text_index
from retrieval.page_mapping import trusted_printed_page


@dataclass
class ClaimDraft:
    claim_id: str
    text: str
    evidence_ids: list[str]
    requires_external_support: bool = True


@dataclass
class VerificationCheck:
    severity: str
    code: str
    message: str
    evidence_id: str | None = None
    claim_id: str | None = None


@dataclass
class VerificationReport:
    status: str
    checks: list[VerificationCheck] = field(default_factory=list)
    semantic_verification_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EvidenceVerifier:
    def __init__(
        self,
        document_maps: dict[str, dict[str, Any]],
        page_indexes: dict[str, PageTextIndex],
    ) -> None:
        self.document_maps = document_maps
        self.page_indexes = page_indexes

    @classmethod
    def from_directories(
        cls,
        document_map_dir: str | Path,
        page_index_dir: str | Path,
    ) -> "EvidenceVerifier":
        maps: dict[str, dict[str, Any]] = {}
        for path in Path(document_map_dir).glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            maps[data["document_id"]] = data
        indexes: dict[str, PageTextIndex] = {}
        for path in Path(page_index_dir).glob("*.json"):
            index = load_page_text_index(path)
            indexes[index.document_id] = index
        return cls(maps, indexes)

    def verify(
        self,
        bundle: EvidenceBundle,
        claims: Iterable[ClaimDraft] = (),
    ) -> VerificationReport:
        checks: list[VerificationCheck] = []
        seen_ids: set[str] = set()

        for item in bundle.evidence:
            if item.evidence_id in seen_ids:
                checks.append(
                    VerificationCheck(
                        "error",
                        "duplicate_evidence_id",
                        "证据 ID 重复",
                        evidence_id=item.evidence_id,
                    )
                )
            seen_ids.add(item.evidence_id)
            checks.extend(self._verify_item(item))

        evidence_by_id = {item.evidence_id: item for item in bundle.evidence}
        for claim in claims:
            checks.extend(_verify_claim_binding(claim, evidence_by_id))

        if any(check.severity == "error" for check in checks):
            status = "fail"
        elif any(check.severity == "warning" for check in checks):
            status = "warning"
        else:
            status = "pass"
        return VerificationReport(status=status, checks=checks)

    def _verify_item(self, item: EvidenceItem) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        document_map = self.document_maps.get(item.document_id)
        index = self.page_indexes.get(item.document_id)
        if document_map is None or index is None:
            return [
                VerificationCheck(
                    "error",
                    "missing_source_index",
                    "证据对应的 DocumentMap 或页索引不存在",
                    evidence_id=item.evidence_id,
                )
            ]

        page = next(
            (record for record in index.pages if record.pdf_page == item.pdf_page),
            None,
        )
        if page is None:
            return [
                VerificationCheck(
                    "error",
                    "missing_page",
                    f"PDF第 {item.pdf_page} 页不存在于索引中",
                    evidence_id=item.evidence_id,
                )
            ]

        current_hash = hashlib.sha256(page.text.encode("utf-8")).hexdigest()
        if current_hash != item.page_text_sha256:
            checks.append(
                VerificationCheck(
                    "error",
                    "page_hash_mismatch",
                    "页文本已变化，证据需要重新抽取",
                    evidence_id=item.evidence_id,
                )
            )

        valid_bounds = 0 <= item.span_start <= item.span_end <= len(page.text)
        if not valid_bounds:
            checks.append(
                VerificationCheck(
                    "error",
                    "invalid_span_bounds",
                    "证据原文偏移超出页文本范围",
                    evidence_id=item.evidence_id,
                )
            )
        elif page.text[item.span_start : item.span_end] != item.evidence_text:
            checks.append(
                VerificationCheck(
                    "error",
                    "evidence_text_mismatch",
                    "证据文本与记录页中的原文不一致",
                    evidence_id=item.evidence_id,
                )
            )

        expected_printed = _printed_page_for(document_map, item.pdf_page)
        if expected_printed != item.printed_page:
            checks.append(
                VerificationCheck(
                    "error",
                    "printed_page_mismatch",
                    f"印刷页应为 {expected_printed!r}，记录为 {item.printed_page!r}",
                    evidence_id=item.evidence_id,
                )
            )

        expected_locator = _expected_locator(document_map, item.pdf_page, expected_printed)
        if expected_locator != item.locator:
            checks.append(
                VerificationCheck(
                    "error",
                    "locator_mismatch",
                    f"引用位置应为“{expected_locator}”",
                    evidence_id=item.evidence_id,
                )
            )

        invalid_context = [
            page_number
            for page_number in item.context_pdf_pages
            if page_number < 1 or page_number > index.page_count
        ]
        if invalid_context:
            checks.append(
                VerificationCheck(
                    "error",
                    "invalid_context_pages",
                    f"上下文页超出范围: {invalid_context}",
                    evidence_id=item.evidence_id,
                )
            )

        if item.matched_terms and not any(
            _normalize(term) in _normalize(page.text) for term in item.matched_terms
        ):
            checks.append(
                VerificationCheck(
                    "warning",
                    "matched_terms_not_found",
                    "记录的命中词未在页文本中复现",
                    evidence_id=item.evidence_id,
                )
            )
        if item.access_level == "local_visual_ocr_unverified":
            checks.append(
                VerificationCheck(
                    "warning",
                    "visual_ocr_unverified",
                    "该证据来自未经人工确认的图片识别，关键数值、上下标和箭头需回看原图",
                    evidence_id=item.evidence_id,
                )
            )
        return checks


def _verify_claim_binding(
    claim: ClaimDraft,
    evidence_by_id: dict[str, EvidenceItem],
) -> list[VerificationCheck]:
    checks: list[VerificationCheck] = []
    if claim.requires_external_support and not claim.evidence_ids:
        return [
            VerificationCheck(
                "error",
                "uncited_claim",
                "需要外部支持的主张没有绑定证据",
                claim_id=claim.claim_id,
            )
        ]

    bound = []
    for evidence_id in claim.evidence_ids:
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            checks.append(
                VerificationCheck(
                    "error",
                    "unknown_evidence_id",
                    f"主张引用了不存在的证据 {evidence_id}",
                    claim_id=claim.claim_id,
                )
            )
        else:
            bound.append(evidence)

    if bound:
        if all(
            item.access_level == "local_visual_ocr_unverified" for item in bound
        ) and _contains_critical_visual_notation(claim.text):
            checks.append(
                VerificationCheck(
                    "error",
                    "critical_claim_visual_only",
                    "含数值、上下标或方向关系的主张不能只依赖未经人工确认的图片识别",
                    claim_id=claim.claim_id,
                )
            )
        combined = "".join(item.evidence_text for item in bound)
        overlap = _bigram_coverage(claim.text, combined)
        if overlap < 0.08:
            checks.append(
                VerificationCheck(
                    "warning",
                    "low_lexical_overlap",
                    "主张与所引证据词面重合度低，必须进行模型语义蕴含复核",
                    claim_id=claim.claim_id,
                )
            )
    return checks


def _contains_critical_visual_notation(value: str) -> bool:
    return bool(re.search(
        r"(?:\d+(?:\.\d+)?\s*(?:mg|g|mmol|mol|%|μm|nm|kda|kd|ml|l)\b|"
        r"[₀-₉⁰-⁹]|[→←↑↓↔]|\b(?:increase|decrease)\b|升高|降低|促进|抑制)",
        value,
        flags=re.IGNORECASE,
    ))


def _expected_locator(
    document_map: dict[str, Any], pdf_page: int, printed_page: int | None
) -> str:
    if document_map["document_type"] == "course_deck":
        return f"幻灯片 {pdf_page}"
    if printed_page is not None:
        return f"教材印刷页 {printed_page}（PDF第 {pdf_page} 页）"
    return f"PDF第 {pdf_page} 页"


def _printed_page_for(document_map: dict[str, Any], pdf_page: int) -> int | None:
    return trusted_printed_page(document_map, pdf_page)


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def _bigram_coverage(left: str, right: str) -> float:
    left_value = _normalize(left)
    right_value = _normalize(right)
    if len(left_value) < 2:
        return 0.0
    grams = {left_value[index : index + 2] for index in range(len(left_value) - 1)}
    return sum(gram in right_value for gram in grams) / len(grams)
