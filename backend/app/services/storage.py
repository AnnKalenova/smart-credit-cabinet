"""Хранилище документов. В прототипе — том Docker; в промышленной версии — S3-совместимое хранилище."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from app.config import get_settings

settings = get_settings()

ALLOWED_TYPES = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "text/plain": ".txt",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/msword": ".doc",
}


EXTENSION_TYPES = {ext: ct for ct, ext in ALLOWED_TYPES.items()} | {".jpeg": "image/jpeg"}

ZIP = b"PK\x03\x04"
OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # контейнер старых форматов Microsoft Office
SIGNATURES = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (ZIP,),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (ZIP,),
    "application/vnd.ms-excel": (OLE,),
    "application/msword": (OLE,),
}


class StorageError(Exception):
    pass


def resolve_type(file_name: str, content_type: str | None) -> str:
    """Тип файла: из заголовка запроса, а если браузер прислал общий тип — по расширению имени."""
    if content_type in ALLOWED_TYPES:
        return content_type
    ext = Path(file_name or "").suffix.lower()
    return EXTENSION_TYPES.get(ext, content_type or "application/octet-stream")


def content_matches(content: bytes, content_type: str) -> bool:
    """Содержимое соответствует заявленному формату (сигнатура файла), а не только расширению."""
    if content_type == "text/plain":
        return b"\x00" not in content
    return content.startswith(SIGNATURES.get(content_type, (b"",)))


def _root() -> Path:
    root = Path(settings.upload_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def save(application_id: uuid.UUID, content: bytes, content_type: str) -> tuple[str, str]:
    if len(content) == 0:
        raise StorageError("Файл пустой")
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise StorageError(f"Файл больше {settings.max_upload_mb} МБ")
    if content_type not in ALLOWED_TYPES:
        raise StorageError("Допустимые форматы: PDF, JPG, PNG, TXT, DOC(X), XLS(X)")
    if not content_matches(content, content_type):
        raise StorageError("Содержимое файла не соответствует его формату. Проверьте, что файл не повреждён")
    key = f"{application_id}/{uuid.uuid4().hex}{ALLOWED_TYPES[content_type]}"
    path = _root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return key, hashlib.sha256(content).hexdigest()


def path_for(key: str) -> Path:
    path = (_root() / key).resolve()
    if _root().resolve() not in path.parents:
        raise StorageError("Недопустимый путь")
    return path


def copy(key: str, application_id: uuid.UUID) -> str:
    """Копия файла для новой заявки: у каждой заявки свои файлы, удаление в одной не затрагивает другую."""
    src = path_for(key)
    new_key = f"{application_id}/{uuid.uuid4().hex}{src.suffix}"
    dst = _root() / new_key
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return new_key


def delete(key: str) -> None:
    try:
        path_for(key).unlink(missing_ok=True)
    except StorageError:
        pass


def demo_document(doc_type_label: str, company_name: str, inn: str) -> bytes:
    """Синтетический документ для демонстрации, если под рукой нет подходящего файла."""
    text = (
        f"СИНТЕТИЧЕСКИЙ ДОКУМЕНТ ДЛЯ ДЕМОНСТРАЦИИ\n\n"
        f"Тип: {doc_type_label}\n"
        f"Компания: {company_name}\n"
        f"ИНН: {inn}\n\n"
        "Документ сформирован прототипом «Умный кредитный кабинет» и не содержит реальных данных.\n"
    )
    return text.encode("utf-8")
