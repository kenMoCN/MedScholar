"""Safe document import manifest with hashing and content deduplication."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


MANIFEST_SCHEMA_VERSION = "1.0"
HASH_CHUNK_SIZE = 1024 * 1024


class DocumentImportError(RuntimeError):
    """Raised when a document cannot be imported safely."""


class ImportStatus(str, Enum):
    PENDING = "pending"
    HASHING = "hashing"
    COPIED = "copied"
    STRUCTURE_INDEXING = "structure_indexing"
    INDEXED = "indexed"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    REMOVED = "removed"


@dataclass
class DocumentImportItem:
    import_id: str
    source_path: str
    original_name: str
    media_type: str
    sha256: str | None = None
    document_id: str | None = None
    stored_path: str | None = None
    status: str = ImportStatus.PENDING.value
    duplicate_of: str | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentImportItem":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: data[key] for key in allowed if key in data})


class DocumentImportManifest:
    def __init__(
        self,
        path: str | Path,
        items: Iterable[DocumentImportItem] = (),
    ) -> None:
        self.path = Path(path)
        self.items = list(items)

    @classmethod
    def load(cls, path: str | Path) -> "DocumentImportManifest":
        manifest_path = Path(path)
        if not manifest_path.exists():
            return cls(manifest_path)

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DocumentImportError(f"无法读取导入清单: {manifest_path}") from exc

        if data.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise DocumentImportError(
                f"不支持的导入清单版本: {data.get('schema_version')!r}"
            )
        raw_items = data.get("items", [])
        if not isinstance(raw_items, list):
            raise DocumentImportError("导入清单的 items 必须是数组")
        return cls(
            manifest_path,
            (DocumentImportItem.from_dict(item) for item in raw_items),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "items": [asdict(item) for item in self.items],
        }
        temp_path = self.path.with_name(
            f".{self.path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp_path, self.path)
        except OSError as exc:
            raise DocumentImportError(f"无法保存导入清单: {self.path}") from exc
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def get(self, import_id: str) -> DocumentImportItem:
        for item in self.items:
            if item.import_id == import_id:
                return item
        raise KeyError(import_id)

    def canonical_for_hash(self, sha256: str) -> DocumentImportItem | None:
        reusable_statuses = {
            ImportStatus.COPIED.value,
            ImportStatus.STRUCTURE_INDEXING.value,
            ImportStatus.INDEXED.value,
        }
        for item in self.items:
            if (
                item.sha256 == sha256
                and item.status in reusable_statuses
                and item.document_id
                and item.stored_path
            ):
                return item
        return None


class DocumentImporter:
    """Queue and copy documents into hash-addressed storage."""

    def __init__(
        self,
        storage_dir: str | Path,
        manifest: DocumentImportManifest,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.manifest = manifest

    def enqueue(self, source_paths: Iterable[str | Path]) -> tuple[DocumentImportItem, ...]:
        now = _utc_now()
        added: list[DocumentImportItem] = []
        for source_path in source_paths:
            source = Path(source_path).expanduser().absolute()
            media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            item = DocumentImportItem(
                import_id=str(uuid.uuid4()),
                source_path=str(source),
                original_name=source.name,
                media_type=media_type,
                created_at=now,
                updated_at=now,
            )
            self.manifest.items.append(item)
            added.append(item)
        self.manifest.save()
        return tuple(added)

    def process_pending(self) -> tuple[DocumentImportItem, ...]:
        processed: list[DocumentImportItem] = []
        for item in self.manifest.items:
            if item.status != ImportStatus.PENDING.value:
                continue
            self._process_item(item)
            processed.append(item)
        return tuple(processed)

    def _process_item(self, item: DocumentImportItem) -> None:
        source = Path(item.source_path)
        try:
            if not source.is_file():
                raise DocumentImportError(f"源文件不存在或不是普通文件: {source}")

            self._set_status(item, ImportStatus.HASHING)
            file_hash = sha256_file(source)
            item.sha256 = file_hash
            item.document_id = document_id_from_hash(file_hash)

            canonical = self.manifest.canonical_for_hash(file_hash)
            if canonical and canonical.import_id != item.import_id:
                item.document_id = canonical.document_id
                item.stored_path = canonical.stored_path
                item.duplicate_of = canonical.import_id
                item.error = None
                self._set_status(item, ImportStatus.DUPLICATE)
                return

            target_dir = self.storage_dir / item.document_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / item.original_name
            temporary_target = target_dir / f".{item.original_name}.{uuid.uuid4().hex}.importing"
            try:
                shutil.copy2(source, temporary_target)
                if sha256_file(temporary_target) != file_hash:
                    raise DocumentImportError("复制后的文件哈希与源文件不一致")
                os.replace(temporary_target, target)
            finally:
                if temporary_target.exists():
                    temporary_target.unlink()

            item.stored_path = str(target.absolute())
            item.duplicate_of = None
            item.error = None
            self._set_status(item, ImportStatus.COPIED)
        except Exception as exc:
            item.error = str(exc)
            self._set_status(item, ImportStatus.FAILED)

    def _set_status(self, item: DocumentImportItem, status: ImportStatus) -> None:
        item.status = status.value
        item.updated_at = _utc_now()
        self.manifest.save()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def document_id_from_hash(sha256: str) -> str:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise DocumentImportError("无效的 SHA-256")
    return f"doc_{sha256}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
