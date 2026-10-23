# THIRD_PARTY_NOTICES

Сторонние компоненты, модели, датасеты и сервисы, которые использует решение, а также смоделированные интеграции. Версии зафиксированы lock-файлами: `backend/requirements.txt`, `integrations-mock/requirements.txt` (с хешами), `frontend/package-lock.json`.

Проверяемая версия не зависит от платных подписок, платных API, покупных данных и закрытых лицензий. Все компоненты ниже распространяются под открытыми лицензиями.

## Модели машинного обучения и LLM

**Не используются.** Кредитное решение принимают детерминированная балльная модель и правила кредитной политики, написанные командой (`backend/app/domain/scoring.py`, `policy.py`). ИИ-сервисы в проверяемой версии не вызываются.

## Датасеты

| Данные | Происхождение |
|---|---|
| Демо-компании, пользователи, обороты по счетам, выписки ЕГРЮЛ, сведения ФНС, кредитные истории (`data/synthetic/companies.json`) | синтетические, созданы генератором `tools/generate_synthetic_data.py`; ИНН/ОГРН с несуществующим кодом «0000» |
| Ключевая ставка Банка России 14% (значение по умолчанию) | открытая информация Банка России (решения от 24.07.2026, 11.09.2026 и 23.10.2026); меняется в интерфейсе |

## Смоделированные интеграции

Реальных интеграций с системами банка и государства нет. Все внешние вызовы идут в симулятор `integrations-mock` (код в репозитории), контракты упрощены:

| Интеграция | Эндпоинт симулятора | Что заменяет |
|---|---|---|
| ЕГРЮЛ / ЕГРИП | `GET /egrul/v1/entities/{inn}` | выписка из реестра (СМЭВ / ФНС) |
| ФНС «Прозрачный бизнес» | `GET /fns/v1/transparent-business/{inn}` | налоговые признаки и отчётность |
| Бюро кредитных историй | `POST /bki/v1/credit-reports` | кредитный отчёт по согласию субъекта (218-ФЗ) |
| АБС банка | `POST /abs/v1/loans` | открытие ссудного счёта и выдача кредита |
| Электронная подпись | код подтверждения внутри кабинета | подпись в ДБО, УКЭП, Госключ |
| Аутентификация | демо-учётки (JWT) | SSO интернет-банка для бизнеса (OIDC) |

Подробнее — [docs/assumptions.md](docs/assumptions.md).

## Образы Docker

| Образ | Лицензия | Назначение |
|---|---|---|
| `postgres:16-alpine` | PostgreSQL License | база данных |
| `python:3.13-slim` | PSF-2.0 (Python), лицензии пакетов Debian | API, воркер, симулятор |
| `nginx:1.27-alpine` | BSD-2-Clause | раздача интерфейса, обратный прокси |
| `node:22-alpine` | MIT | только сборка интерфейса (в итоговый образ не входит) |

## Бэкенд и симулятор (Python)

| Пакет | Версия | Лицензия |
|---|---|---|
| fastapi | 0.161.0 | MIT |
| starlette | 1.9.0 | BSD-3-Clause |
| uvicorn (+ httptools, uvloop, watchfiles, websockets) | 0.54.0 | BSD-3-Clause (httptools, uvloop, watchfiles — MIT; websockets — BSD-3-Clause) |
| pydantic, pydantic-core, pydantic-settings | 2.14.1, 2.50.1, 2.16.1 | MIT |
| sqlalchemy | 2.1.6 | MIT |
| alembic (+ mako, markupsafe) | 1.20.1 | MIT (markupsafe — BSD-3-Clause) |
| psycopg, psycopg-binary | 3.3.6 | LGPL-3.0-only (используется как библиотека без изменений) |
| httpx, httpcore | 0.28.1, 1.0.9 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| anyio | 4.15.1 | MIT |
| idna | 3.20 | BSD-3-Clause |
| certifi | 2026.10.19 | MPL-2.0 |
| pyjwt | 2.15.1 | MIT |
| python-multipart | 0.0.32 | Apache-2.0 |
| prometheus-client | 0.26.0 | Apache-2.0 AND BSD-2-Clause |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| pyyaml | 6.0.3 | MIT |
| click | 8.5.0 | BSD-3-Clause |
| typing-extensions | 4.16.0 | PSF-2.0 |
| tzdata | 2026.5 | Apache-2.0 |
| typing-inspection, annotated-types, annotated-doc | 0.4.4, 0.8.0, 0.0.5 | MIT |

Только для разработки и тестов (`backend/requirements-dev.txt`): pytest 9.1.1 (MIT), pluggy (MIT), iniconfig (MIT), packaging (Apache-2.0 OR BSD-2-Clause), pygments (BSD-2-Clause), colorama 0.4.6 (BSD-3-Clause, только в Windows), ruff 0.17.2 (MIT).

## Интерфейс (npm)

| Пакет | Версия | Лицензия |
|---|---|---|
| react, react-dom, scheduler | 19.3.1, 0.28.0 | MIT |
| react-router, react-router-dom (+ cookie, set-cookie-parser) | 7.18.4 | MIT |
| @tanstack/react-query, @tanstack/query-core | 5.104.5 | MIT |
| lucide-react (иконки) | 1.61.0 | ISC |
| @fontsource-variable/golos-text — шрифт Golos Text (ParaType) | 5.3.0 | SIL Open Font License 1.1 |
| @types/react, csstype | 19.3.0, 3.2.3 | MIT |

Инструменты сборки (`devDependencies`, в итоговый образ не входят): vite 7.3.7, @vitejs/plugin-react 5.2.0, typescript 5.9.3 и их зависимости — 114 пакетов под MIT, ISC, Apache-2.0, BSD-3-Clause, CC-BY-4.0 (данные caniuse-lite).

## Визуальный стиль

Интерфейс оформлен в духе цифровых продуктов Альфа-Банка: палитра (фирменный красный, графитовый фон, белые карточки), скругления и типографика опираются на открытые дизайн-токены библиотеки [core-components](https://github.com/core-ds/core-components) (MIT, © core-ds). Код библиотеки в решение не входит — стили написаны командой. Логотип и товарные знаки банка не используются: знак кабинета (три ступени «конвейера» на красной плашке) и иконка приложения нарисованы командой.

## Инструменты разработки (не входят в проверяемую версию)

| Инструмент | Лицензия | Для чего |
|---|---|---|
| uv | MIT / Apache-2.0 | генерация lock-файлов Python с хешами |
| Playwright | Apache-2.0 | UI-прогон сценариев и скриншоты (`e2e/ui_tour.py`) |
| newman | Apache-2.0 | запуск Postman-коллекции из командной строки |
| mermaid | MIT | рендер диаграмм для документации (`tools/render_diagrams.py`) |
| [UI/UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill), [Taste](https://github.com/Leonxlnx/taste-skill) | MIT | чек-листы по интерфейсам (контраст, состояния, мобильная вёрстка), по которым проверялся дизайн |
| GitHub Actions | сервис GitHub | CI: линтер, тесты, сборка, запуск в Docker |
| Claude (Anthropic) | коммерческий сервис | ИИ-ассистент при написании кода и документации; в решение не встроен |
