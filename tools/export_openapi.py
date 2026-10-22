"""Выгрузка спецификаций OpenAPI в docs/ без запуска серверов.

    cd backend && python ../tools/export_openapi.py

Спецификации генерируются из кода (FastAPI + Pydantic), поэтому копии в docs/ всегда совпадают
с тем, что показывает Swagger на поднятом стенде (/api/docs и :8001/docs).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def _write(spec: dict, name: str) -> None:
    path = DOCS / name
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Записано: {path} ({len(spec['paths'])} путей)")


def main() -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app.main import app as api  # импорт после добавления backend в sys.path

    _write(api.openapi(), "openapi.json")

    spec = importlib.util.spec_from_file_location(
        "integrations_mock_main", ROOT / "integrations-mock" / "app" / "main.py"
    )
    mock = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mock  # Pydantic разрешает аннотации моделей через модуль
    spec.loader.exec_module(mock)
    _write(mock.app.openapi(), "openapi-integrations-mock.json")


if __name__ == "__main__":
    main()
