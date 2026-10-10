"""Local conversation history and per-session image attachments."""

from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConversationMessage:
    role: str
    title: str
    body: str
    tone: str
    created_at: str = field(default_factory=_now)
    resources: list[dict] = field(default_factory=list)
    question_context: str = ""
    attachments: list[dict] = field(default_factory=list)


@dataclass
class ConversationSession:
    session_id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ConversationMessage] = field(default_factory=list)


def conversation_context_for_session(
    session: ConversationSession,
    *,
    recent_limit: int = 10,
) -> list[dict[str, str]]:
    """Keep recent turns plus the latest saved question anchor, without images."""
    messages = [item for item in session.messages if item.role in {"user", "assistant"}]
    recent = messages[-recent_limit:]
    anchor = next(
        (item for item in reversed(messages) if item.question_context),
        None,
    )
    if anchor is not None and anchor not in recent:
        recent = [anchor, *recent]
    return [
        {
            "role": item.role,
            "body": item.body,
            "question_context": item.question_context,
        }
        for item in recent
    ]


class ConversationStore:
    """Persist text and locally retained images; never persist credentials."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else None
        self._lock = RLock()
        self._sessions: dict[str, ConversationSession] = {}
        self._load()

    def list_sessions(self) -> tuple[ConversationSession, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._sessions.values(),
                    key=lambda item: item.updated_at,
                    reverse=True,
                )
            )

    def create_session(self, title: str = "新对话") -> ConversationSession:
        with self._lock:
            timestamp = _now()
            session = ConversationSession(
                session_id=uuid4().hex,
                title=title,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self._sessions[session.session_id] = session
            self._save()
            return session

    def get_session(self, session_id: str) -> ConversationSession:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"会话不存在: {session_id}") from exc

    def delete_session(self, session_id: str) -> ConversationSession:
        """Delete one session and its dedicated local image directory."""
        with self._lock:
            try:
                session = self._sessions[session_id]
            except KeyError as exc:
                raise KeyError(f"会话不存在: {session_id}") from exc
            folder = self._image_dir(session_id)
            if folder is not None and folder.exists():
                if folder.is_symlink() or not folder.is_dir():
                    raise ValueError("会话图片目录不是安全的普通目录")
                for entry in folder.iterdir():
                    if entry.is_dir() and not entry.is_symlink():
                        raise ValueError("会话图片目录包含非预期的子目录，已停止删除")
                for entry in folder.iterdir():
                    entry.unlink()
                folder.rmdir()
            self._sessions.pop(session_id)
            self._save()
            return session

    def _image_dir(self, session_id: str) -> Path | None:
        if self.path is None:
            return None
        if not re.fullmatch(r"[0-9a-f]{32}", str(session_id)):
            raise ValueError("无效的会话标识")
        root = self.path.parent / "conversation_images"
        if root.is_symlink():
            raise ValueError("会话图片根目录不是安全的普通目录")
        folder = root / session_id
        if folder.parent.resolve() != root.resolve():
            raise ValueError("会话图片路径越界")
        return folder

    def save_image(self, session_id: str, source: str | Path) -> dict[str, str]:
        """Copy an uploaded image into the current session's private folder."""
        with self._lock:
            self.get_session(session_id)
            folder = self._image_dir(session_id)
            if folder is None:
                raise ValueError("未配置持久化会话目录，无法保留图片")
            image = Path(source).resolve()
            suffix = image.suffix.lower()
            if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                raise ValueError("只支持保存图片附件")
            if not image.is_file() or image.stat().st_size > 30 * 1024 * 1024:
                raise ValueError("图片不存在或超过 30 MB")
            folder.mkdir(parents=True, exist_ok=True)
            if folder.is_symlink():
                raise ValueError("会话图片目录不是安全的普通目录")
            name = uuid4().hex + suffix
            target = folder / name
            temporary = folder / (name + ".tmp")
            try:
                shutil.copyfile(image, temporary)
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return {"kind": "image", "stored_name": name, "original_name": image.name}

    def resolve_image(self, session_id: str, attachment: dict) -> Path | None:
        """Resolve only an image owned by this session, never an arbitrary path."""
        folder = self._image_dir(session_id)
        if folder is None or folder.is_symlink():
            return None
        name = str(attachment.get("stored_name", ""))
        if not re.fullmatch(r"[0-9a-f]{32}\.(?:png|jpg|jpeg|webp|bmp)", name):
            return None
        candidate = folder / name
        if candidate.is_symlink() or candidate.parent.resolve() != folder.resolve():
            return None
        return candidate if candidate.is_file() else None

    def latest_image_for_topic(
        self,
        session_id: str,
        topic: str = "",
    ) -> Path | None:
        """Find an image attached to the active answered question, if any."""
        with self._lock:
            session = self.get_session(session_id)
            active_topic = str(topic or "").strip()
            if not active_topic:
                active_topic = next(
                    (
                        item.question_context
                        for item in reversed(session.messages)
                        if item.role == "assistant" and item.question_context
                    ),
                    "",
                )
            if not active_topic:
                return None
            seen_topic = False
            for item in reversed(session.messages):
                if item.role == "assistant" and item.question_context:
                    if item.question_context != active_topic:
                        if seen_topic:
                            break
                        continue
                    seen_topic = True
                if item.role == "user" and seen_topic:
                    for attachment in reversed(item.attachments):
                        if attachment.get("kind") == "image":
                            return self.resolve_image(session_id, attachment)
            return None

    def append_message(
        self,
        session_id: str,
        *,
        role: str,
        title: str,
        body: str,
        tone: str,
        resources: list[dict] | tuple[dict, ...] | None = None,
        question_context: str = "",
        attachments: list[dict] | tuple[dict, ...] | None = None,
    ) -> ConversationMessage:
        if role not in {"user", "assistant", "system"}:
            raise ValueError(f"不支持的消息角色: {role}")
        clean_body = str(body).strip()
        if not clean_body:
            raise ValueError("不能保存空消息")
        with self._lock:
            session = self.get_session(session_id)
            message = ConversationMessage(
                role=role,
                title=str(title).strip() or role,
                body=clean_body,
                tone=str(tone).strip() or "neutral",
                resources=[dict(item) for item in (resources or ())],
                question_context=str(question_context or "").strip()[:6000],
                attachments=[dict(item) for item in (attachments or ())],
            )
            session.messages.append(message)
            session.updated_at = message.created_at
            if role == "user" and session.title == "新对话":
                first_line = clean_body.splitlines()[0].strip()
                session.title = first_line[:24] + ("…" if len(first_line) > 24 else "")
            self._save()
            return message

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            raw_sessions = data.get("sessions", [])
            for raw_session in raw_sessions:
                messages = [
                    ConversationMessage(**raw_message)
                    for raw_message in raw_session.get("messages", [])
                ]
                session = ConversationSession(
                    session_id=str(raw_session["session_id"]),
                    title=str(raw_session.get("title", "新对话")),
                    created_at=str(raw_session["created_at"]),
                    updated_at=str(raw_session["updated_at"]),
                    messages=messages,
                )
                self._sessions[session.session_id] = session
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError(f"无法读取本地会话历史: {self.path}") from exc

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": "1.0",
            "privacy": "local_session_images_no_secrets_no_hidden_reasoning",
            "sessions": [asdict(item) for item in self.list_sessions()],
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, self.path)
