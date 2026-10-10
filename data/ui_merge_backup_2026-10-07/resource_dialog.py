"""Focused browser for all external resources attached to one answer."""

from __future__ import annotations

import html
from typing import Any, Iterable

from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QTextBrowser, QVBoxLayout

from dialog_theme import apply_dialog_theme, mark_button
from evidence_labels import friendly_resource_intro


class ResourceDialog(QDialog):
    def __init__(self, resources: Iterable[dict[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self.resources = tuple(resources)
        self.setWindowTitle("本次检索的全部来源")
        self.resize(920, 680)
        self.setMinimumSize(720, 500)
        apply_dialog_theme(self)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        title = QLabel("全部来源与候选资料")
        title.setObjectName("DialogTitle")
        layout.addWidget(title)
        hint = QLabel(
            f"本次共检索到 {len(self.resources)} 条。候选资料不等同于最终答案依据；"
            "请以回答正文中的引用和核验说明为准。"
        )
        hint.setObjectName("InfoBanner")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(
            "QTextBrowser { background:white; border:1px solid #DCE6EB; "
            "border-radius:10px; padding:14px; }"
        )
        for index, resource in enumerate(self.resources, start=1):
            browser.append(_resource_html(index, resource))
        layout.addWidget(browser, 1)

        close_button = QPushButton("关闭")
        mark_button(close_button, "primary")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button, 0)


def _resource_html(index: int, resource: dict[str, Any]) -> str:
    original_title = str(resource.get("title") or "未命名资料")
    chinese_title = str(resource.get("title_zh") or "").strip()
    title = html.escape(chinese_title or original_title)
    raw_url = str(resource.get("url") or "")
    safe_url = html.escape(raw_url, quote=True)
    linked_title = (
        f'<a href="{safe_url}" style="color:#087563;text-decoration:none;">'
        f"{title}</a>"
        if raw_url.startswith(("https://", "http://"))
        else title
    )
    source = html.escape(str(resource.get("source") or ""))
    year = html.escape(str(resource.get("year") or "年份未知"))
    intro = html.escape(friendly_resource_intro(
        resource.get("description"),
        resource.get("evidence_level"),
        resource.get("publication_types", ()),
        resource.get("authority_reason"),
    ))
    authority = html.escape(str(resource.get("authority_reason") or ""))
    verified = " · 多个数据库记录一致" if resource.get("cross_validated") else ""
    direct = " · 可作为已核验答复来源" if resource.get("may_determine_answer") else " · 候选资料"
    details = " · ".join(item for item in (source, year) if item)
    details += f"<br><b>资料简介：</b>{intro}"
    if authority:
        details += f"<br>{authority}"
    original_line = (
        f'<br><span style="color:#71858E;font-size:10px;">{html.escape(original_title)}</span>'
        if chinese_title and chinese_title != original_title
        else ""
    )
    return (
        '<div style="margin:0 0 10px 0;padding:12px 14px;background:#F5F9F8;'
        'border-left:3px solid #19A58C;">'
        f'<span style="color:#71858E;font-size:10px;">{index:02d}{direct}{verified}</span><br>'
        f"<b>{linked_title}</b>{original_line}<br><span>{details}</span></div>"
    )
