"""Validation helpers for textbook printed-page mappings."""

from __future__ import annotations

from typing import Any


def trusted_page_map_segments(document_map: dict[str, Any]) -> list[dict[str, Any]]:
    """Return segments only when the complete map passes consistency checks."""

    raw = document_map.get("page_map_segments", [])
    if not isinstance(raw, list) or not raw:
        return []
    page_count = int(document_map.get("page_count", 0) or 0)
    segments: list[dict[str, Any]] = []
    previous_end = 0
    for item in raw:
        try:
            start = int(item["pdf_start"])
            end = int(item["pdf_end"])
            offset = int(item["offset"])
            # Legacy indexes did not persist confidence. A structurally valid
            # segment remains usable; newly generated segments carry a score.
            confidence = float(item.get("confidence", 1.0))
        except (KeyError, TypeError, ValueError):
            return []
        if start < 1 or end < start or (page_count and end > page_count):
            return []
        if start <= previous_end or confidence < 0.8:
            return []
        if start - offset < 1:
            return []
        previous_end = end
        segments.append(item)
    if len(segments) > max(12, page_count // 50 if page_count else 12):
        return []
    return segments


def trusted_printed_page(
    document_map: dict[str, Any], pdf_page: int
) -> int | None:
    for segment in trusted_page_map_segments(document_map):
        if int(segment["pdf_start"]) <= pdf_page <= int(segment["pdf_end"]):
            printed = pdf_page - int(segment["offset"])
            return printed if printed >= 1 else None
    return None
