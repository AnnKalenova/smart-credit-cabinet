"""Хранилище документов. На стенде — том Docker; в промышленной эксплуатации — S3-совместимое хранилище."""

from __future__ import annotations

import hashlib
import unicodedata
import uuid
from pathlib import Path

from fastapi import HTTPException, status
from fastapi.responses import FileResponse

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
    """Тип файла: по расширению имени (браузеры и ОС подписывают один формат по-разному), иначе — из
    заголовка запроса. Содержимое затем сверяется с типом по сигнатуре."""
    ext = Path(file_name or "").suffix.lower()
    if ext in EXTENSION_TYPES:
        return EXTENSION_TYPES[ext]
    return content_type or "application/octet-stream"


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


def safe_file_name(file_name: str, content_type: str) -> str:
    """Имя файла для хранения и скачивания. Управляющие и «невидимые» символы (в том числе разворот
    текста справа налево) удаляются, расширение берётся из проверенного типа: «Счёт.hta», присланный как
    текст, скачается как «Счёт.hta.txt» и не запустится двойным щелчком."""
    name = "".join(ch for ch in (file_name or "") if unicodedata.category(ch)[0] != "C")
    name = name.replace("/", "_").replace("\\", "_").strip(" .")
    ext = ALLOWED_TYPES.get(content_type, "")
    stem, dot, suffix = name.rpartition(".")
    if dot and "." + suffix.lower() == ext:
        name = stem.strip(" .")
    elif dot and "." + suffix.lower() == ".jpeg" and ext == ".jpg":
        name, ext = stem.strip(" .") or "document", ".jpeg"
    name = (name or "document")[: 255 - len(ext)]
    return name + ext


def exists(key: str) -> bool:
    try:
        return path_for(key).is_file()
    except StorageError:
        return False


def file_response(doc) -> FileResponse:
    """Ответ со скачиванием документа. Если файла нет в хранилище — понятная ошибка 404, а не 500."""
    if not exists(doc.storage_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не найден в хранилище")
    return FileResponse(path_for(doc.storage_key), media_type=doc.content_type, filename=doc.file_name)


def path_for(key: str) -> Path:
    path = (_root() / key).resolve()
    if _root().resolve() not in path.parents:
        raise StorageError("Недопустимый путь")
    return path


def delete(key: str) -> None:
    try:
        path_for(key).unlink(missing_ok=True)
    except StorageError:
        pass


def demo_document(doc_type_label: str, company_name: str, inn: str) -> bytes:
    """Пример документа для демо-стенда, если под рукой нет подходящего файла."""
    text = (
        "ПРИМЕР ДОКУМЕНТА\n\n"
        f"Тип: {doc_type_label}\n"
        f"Компания: {company_name}\n"
        f"ИНН: {inn}\n\n"
        "Сформирован на демо-стенде «Умного кредитного кабинета» и не содержит реальных данных.\n"
    )
    return text.encode("utf-8")
