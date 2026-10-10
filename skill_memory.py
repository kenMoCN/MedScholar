"""Human-reviewed local skill memory for reusable tutoring guidance."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from agent_orchestrator import AgentRunResult


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SkillKind(str, Enum):
    MEDICAL = "medical"
    WORKFLOW = "workflow"


class SkillStatus(str, Enum):
    PENDING = "pending_review"
    ACTIVE = "active"
    REJECTED = "rejected"


@dataclass(frozen=True)
class SkillEvidenceAnchor:
    document_id: str
    original_name: str
    pdf_page: int
    locator: str
    section_title: str | None
    page_text_sha256: str
    excerpt: str


@dataclass
class SkillRecord:
    skill_id: str
    kind: str
    status: str
    question: str
    guidance: str
    trigger_terms: tuple[str, ...]
    evidence: tuple[SkillEvidenceAnchor, ...]
    created_at: str
    reviewed_at: str | None = None
    review_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SkillStore:
    """Atomic JSON store. Only active, reviewed records can be recalled."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else None
        self._lock = RLock()
        self._records: dict[str, SkillRecord] = {}
        self._load()

    def create_candidate(
        self,
        *,
        question: str,
        guidance: str,
        kind: str | SkillKind,
        trigger_terms: tuple[str, ...] | list[str] = (),
        evidence: tuple[SkillEvidenceAnchor, ...] | list[SkillEvidenceAnchor] = (),
    ) -> SkillRecord:
        clean_question = _bounded_text(question, "原问题", 2000)
        clean_guidance = _bounded_text(guidance, "纠错或指导内容", 2000)
        normalized_kind = SkillKind(kind).value
        terms = _normalize_terms(trigger_terms) or tuple(_extract_terms(clean_question))
        if not terms:
            raise ValueError("至少需要一个有效触发词")
        with self._lock:
            self._load()
            record = SkillRecord(
                skill_id=f"skill_{uuid4().hex}",
                kind=normalized_kind,
                status=SkillStatus.PENDING.value,
                question=clean_question,
                guidance=clean_guidance,
                trigger_terms=terms[:12],
                evidence=tuple(evidence),
                created_at=_now(),
            )
            self._records[record.skill_id] = record
            self._save()
            return record

    def list_records(self, status: str | SkillStatus | None = None) -> tuple[SkillRecord, ...]:
        with self._lock:
            self._load()
            status_value = SkillStatus(status).value if status is not None else None
            records = [
                item for item in self._records.values()
                if status_value is None or item.status == status_value
            ]
            return tuple(sorted(records, key=lambda item: item.created_at, reverse=True))

    def approve(self, skill_id: str, *, review_note: str = "") -> SkillRecord:
        with self._lock:
            self._load()
            record = self._require(skill_id)
            if record.status != SkillStatus.PENDING.value:
                raise ValueError("只有待审核 Skill 可以通过审核")
            if record.kind == SkillKind.MEDICAL.value and not record.evidence:
                raise ValueError("医学知识 Skill 必须绑定可追溯证据后才能启用")
            record.status = SkillStatus.ACTIVE.value
            record.reviewed_at = _now()
            record.review_note = str(review_note).strip()[:1000]
            self._save()
            return record

    def reject(self, skill_id: str, *, review_note: str = "") -> SkillRecord:
        with self._lock:
            self._load()
            record = self._require(skill_id)
            if record.status != SkillStatus.PENDING.value:
                raise ValueError("只有待审核 Skill 可以驳回")
            record.status = SkillStatus.REJECTED.value
            record.reviewed_at = _now()
            record.review_note = str(review_note).strip()[:1000]
            self._save()
            return record

    def match(self, query: str, *, limit: int = 4) -> tuple[SkillRecord, ...]:
        haystack = _normalize(str(query))
        if not haystack:
            return ()
        ranked = []
        for record in self.list_records(SkillStatus.ACTIVE):
            hits = sum(_normalize(term) in haystack for term in record.trigger_terms)
            if hits:
                ranked.append((hits / len(record.trigger_terms), hits, record))
        ranked.sort(key=lambda item: (item[0], item[1], item[2].reviewed_at or ""), reverse=True)
        return tuple(item[2] for item in ranked[: max(1, limit)])

    def _require(self, skill_id: str) -> SkillRecord:
        try:
            return self._records[skill_id]
        except KeyError as exc:
            raise KeyError(f"Skill 不存在: {skill_id}") from exc

    def _load(self) -> None:
        if self.path is None:
            return
        self._records = {}
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("schema_version") != "1.0":
                raise ValueError("不支持的 Skill 存储版本")
            for raw in payload.get("skills", []):
                evidence = tuple(
                    SkillEvidenceAnchor(**item) for item in raw.get("evidence", [])
                )
                record = SkillRecord(
                    skill_id=str(raw["skill_id"]),
                    kind=SkillKind(raw["kind"]).value,
                    status=SkillStatus(raw["status"]).value,
                    question=str(raw["question"]),
                    guidance=str(raw["guidance"]),
                    trigger_terms=tuple(str(x) for x in raw.get("trigger_terms", [])),
                    evidence=evidence,
                    created_at=str(raw["created_at"]),
                    reviewed_at=(str(raw["reviewed_at"]) if raw.get("reviewed_at") else None),
                    review_note=str(raw.get("review_note", "")),
                )
                self._records[record.skill_id] = record
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取本地 Skill 存储: {self.path}") from exc

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "safety": "human_reviewed_no_hidden_reasoning_no_secrets",
            "skills": [item.to_dict() for item in self._records.values()],
        }
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


def evidence_anchors_from_result(result: AgentRunResult | None) -> tuple[SkillEvidenceAnchor, ...]:
    if result is None or result.status != "completed" or result.evidence_bundle is None:
        return ()
    return tuple(
        SkillEvidenceAnchor(
            document_id=item.document_id,
            original_name=item.original_name,
            pdf_page=item.pdf_page,
            locator=item.locator,
            section_title=item.section_title,
            page_text_sha256=item.page_text_sha256,
            excerpt=item.evidence_text[:600],
        )
        for item in result.evidence_bundle.evidence
    )


def skill_prompt_records(records: tuple[SkillRecord, ...]) -> list[dict[str, Any]]:
    return [
        {
            "skill_id": item.skill_id,
            "kind": item.kind,
            "guidance": item.guidance,
            "trigger_terms": list(item.trigger_terms),
            "evidence_anchors": [
                {
                    "document_id": anchor.document_id,
                    "pdf_page": anchor.pdf_page,
                    "page_text_sha256": anchor.page_text_sha256,
                }
                for anchor in item.evidence
            ],
        }
        for item in records
    ]


def _bounded_text(value: str, label: str, limit: int) -> str:
    clean = str(value).strip()
    if not clean:
        raise ValueError(f"{label}不能为空")
    if len(clean) > limit:
        raise ValueError(f"{label}不能超过 {limit} 个字符")
    return clean


def _normalize_terms(values) -> tuple[str, ...]:
    result = []
    for value in values:
        term = str(value).strip().lower()
        if len(term) >= 2 and term not in result:
            result.append(term)
    return tuple(result)


def _extract_terms(value: str) -> list[str]:
    cleaned = str(value)
    for marker in (
        "请问", "请解释", "解释一下", "解释", "如何", "怎么", "为什么",
        "是什么", "形成机制", "形成", "机制", "作用", "一下", "帮我",
    ):
        cleaned = cleaned.replace(marker, " ")
    return list(dict.fromkeys(
        item.lower() for item in re.findall(
            r"[A-Za-z][A-Za-z0-9-]{2,}|[\u4e00-\u9fff]{2,12}", cleaned
        )
    ))


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())
