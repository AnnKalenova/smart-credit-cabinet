"""Хранилище документов (тип и сигнатура файла) и файл-пульс воркера для healthcheck."""

import os

import pytest

from app.services import storage
from app.worker import runner

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLS = "application/vnd.ms-excel"


def test_type_is_taken_from_header_or_extension():
    assert storage.resolve_type("a.pdf", PDF) == PDF
    assert storage.resolve_type("Скан.JPEG", "application/octet-stream") == "image/jpeg"
    assert storage.resolve_type("report.docx", "") == DOCX
    assert storage.resolve_type("tool.exe", "application/octet-stream") == "application/octet-stream"
    assert storage.resolve_type("", None) == "application/octet-stream"


@pytest.mark.parametrize(
    "content,content_type,ok",
    [
        (b"%PDF-1.7 ...", PDF, True),
        (b"MZ\x90\x00", PDF, False),
        (b"\xff\xd8\xff\xe0 jfif", "image/jpeg", True),
        (b"\x89PNG\r\n\x1a\n....", "image/png", True),
        (b"GIF89a", "image/png", False),
        (b"PK\x03\x04....", DOCX, True),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1....", XLS, True),
        (b"PK\x03\x04....", XLS, False),
        ("Пояснение к выручке".encode("cp1251"), "text/plain", True),
        (b"bin\x00ary", "text/plain", False),
    ],
)
def test_content_must_match_declared_format(content, content_type, ok):
    assert storage.content_matches(content, content_type) is ok


def test_heartbeat_file_is_refreshed_with_throttling(tmp_path, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    path = tmp_path / "worker-heartbeat"
    beat = runner.Heartbeat(str(path), every_seconds=5)

    beat.beat()
    assert path.exists()
    os.utime(path, (0, 0))  # «старый» пульс
    clock[0] += 2
    beat.beat()  # чаще раза в 5 с файл не трогаем
    assert path.stat().st_mtime == 0
    clock[0] += 5
    beat.beat()
    assert path.stat().st_mtime > 0


def test_heartbeat_survives_unwritable_location(tmp_path):
    beat = runner.Heartbeat(str(tmp_path / "missing-dir" / "hb"))
    beat.beat()  # ошибка записи не останавливает воркер
    assert not (tmp_path / "missing-dir").exists()
