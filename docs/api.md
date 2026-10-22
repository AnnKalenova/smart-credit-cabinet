# Описание API

REST API, JSON, версия `v1`. Спецификация OpenAPI 3.1 генерируется из кода.

| Что | Где |
|---|---|
| Swagger UI | http://localhost:8080/api/docs (через nginx) или http://localhost:8000/api/docs (напрямую) |
| ReDoc | http://localhost:8080/api/redoc |
| OpenAPI JSON | http://localhost:8080/api/openapi.json, копия в репозитории — [openapi.json](openapi.json) |
| Симулятор внешних систем | http://localhost:8001/docs, копия — [openapi-integrations-mock.json](openapi-integrations-mock.json) |
| Коллекция запросов Postman | [postman/smart-credit-cabinet.postman_collection.json](postman/smart-credit-cabinet.postman_collection.json) |

## Аутентификация

```http
POST /api/v1/auth/login
{"login": "smirnov", "password": "demo1234"}
→ {"access_token": "eyJ…", "token_type": "bearer", "user": {...}}
```

Дальше токен передаётся в заголовке `Authorization: Bearer <access_token>`. В Swagger — кнопка **Authorize**. Список демо-учёток: `GET /api/v1/auth/demo-accounts`.

| Роль | Доступ |
|---|---|
| `CLIENT` | `/api/v1/applications/*`, `/companies/*`, `/products/*`, `/loans/*`, `/notifications/*` — только по своим компаниям |
| `ANALYST` | `/api/v1/bank/*`, кроме изменения параметров |
| `RISK_MANAGER` | `/api/v1/bank/*`, включая `PUT /bank/settings/{key}` |

## Ошибки

| Код | Когда | Тело |
|---|---|---|
| 401 | нет токена или он истёк; неверный логин или пароль | `{"detail": "Требуется вход в систему"}` |
| 403 | не та роль или нет права подписи | `{"detail": "Выбрать условия кредита может руководитель или представитель по доверенности…"}` |
| 404 | объект не найден или принадлежит другой компании | `{"detail": "Заявка не найдена"}` |
| 409 | действие недоступно в текущем статусе, конфликт версий, истёк срок предложения | `{"detail": "Действие недоступно: заявка в статусе «Готово предложение». Обновите страницу"}` |
| 413 | файл больше `MAX_UPLOAD_MB` | `{"detail": "Файл больше 10 МБ"}` |
| 422 | ошибка валидации или бизнес-проверки | `{"detail": {"message": "Проверьте введённые данные", "problems": ["Сумма: должно быть не больше 10 000 000 000"]}}` |
| 429 | 10 неудачных попыток входа подряд (вход по логину закрыт на 10 минут), больше 10 кодов подписи за 10 минут, 5 неверных вводов кода | `{"detail": "Слишком много неудачных попыток входа. Попробуйте через 10 минут"}` |
| 500 | непредвиденная ошибка | `{"detail": "Внутренняя ошибка сервиса. Попробуйте ещё раз", "request_id": "…"}` |

Ответы с ошибками валидации и бизнес-проверки содержат список `problems` на русском — интерфейс показывает его пользователю как есть. Каждый ответ несёт заголовок `X-Request-ID`: по нему запрос находится в JSON-логах API.

## Эндпоинты

### Клиент: компания, продукты, кредиты

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/auth/me` | текущий пользователь и его компании (должность, право подписи) |
| GET | `/api/v1/dictionaries` | цели кредита, типы документов, названия статусов, ключевая ставка |
| GET | `/api/v1/products` | каталог продуктов |
| GET | `/api/v1/companies/{company_id}` | профиль компании, обороты по счёту, предварительный лимит |
| POST | `/api/v1/products/match` | подбор продукта по цели, сумме и сроку: ставка, платёж, документы |
| GET | `/api/v1/loans?company_id=` | выданные кредиты |
| GET | `/api/v1/loans/{loan_id}` | кредит с графиком платежей |
| GET | `/api/v1/notifications` | уведомления в кабинете |
| POST | `/api/v1/notifications/read-all` | отметить прочитанными |

### Клиент: заявка

| Метод | Путь | Назначение | Статус заявки |
|---|---|---|---|
| GET | `/api/v1/applications?company_id=` | список заявок компании | любой |
| POST | `/api/v1/applications` | создать черновик (данные компании и выручка подставляются) | → DRAFT |
| GET | `/api/v1/applications/{id}` | заявка: статус, «сейчас / что дальше», предложения, объяснение решения, история, права | любой |
| PATCH | `/api/v1/applications/{id}` | изменить черновик (автосохранение) | DRAFT |
| POST | `/api/v1/applications/{id}/documents` | загрузить документ (multipart: `doc_type`, `file`; PDF, JPG, PNG, TXT, DOC(X), XLS(X) до 10 МБ, формат сверяется с содержимым файла) | DRAFT, NEEDS_INFO |
| POST | `/api/v1/applications/{id}/documents/demo?doc_type=` | приложить синтетический документ | DRAFT, NEEDS_INFO |
| GET / DELETE | `/api/v1/applications/{id}/documents/{doc_id}` | скачать / удалить документ | — |
| POST | `/api/v1/applications/{id}/sign-challenges` | получить код подписи (`CONSENTS` или `CONTRACT`) | DRAFT / SIGNING |
| POST | `/api/v1/applications/{id}/consents` | подписать согласия на запрос в БКИ и обработку данных | DRAFT |
| POST | `/api/v1/applications/{id}/request-signature` | передать на подпись руководителю (для бухгалтера) | DRAFT, OFFER_READY, SIGNING |
| POST | `/api/v1/applications/{id}/submit` | отправить заявку | DRAFT → PRECHECK |
| POST | `/api/v1/applications/{id}/info-response` | ответить на запрос банка (комментарий, поля); запрошенные документы прикладываются после запроса; сумму и срок меняет только подписант | NEEDS_INFO → PRECHECK |
| POST | `/api/v1/applications/{id}/cancel` | отозвать заявку (подписант) или отменить свой черновик | до решения → CANCELLED |
| POST | `/api/v1/applications/{id}/offers/{offer_id}/accept` | выбрать вариант предложения (подписант) | OFFER_READY → SIGNING |
| POST | `/api/v1/applications/{id}/offers/decline` | отказаться от предложения (подписант) | OFFER_READY → CLIENT_DECLINED |
| POST | `/api/v1/applications/{id}/signing/back` | вернуться к выбору условий (подписант); если срок предложения истёк — 409 и EXPIRED | SIGNING → OFFER_READY |
| GET | `/api/v1/applications/{id}/contract` | проект договора и полный график платежей | SIGNING и далее |
| POST | `/api/v1/applications/{id}/sign` | подписать договор кодом; по истёкшему предложению — 409 и EXPIRED | SIGNING → DISBURSEMENT |
| POST | `/api/v1/applications/{id}/reapply` | новая заявка на основе закрытой: параметры, залог и копии документов переносятся | закрытые |
| GET / POST | `/api/v1/applications/{id}/messages` | переписка с банком | любой |

### Банк

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/api/v1/bank/applications?scope=queue\|active\|mine\|all&status=&product=&q=` | очередь ручного рассмотрения и реестр |
| GET | `/api/v1/bank/applications/{id}` | досье: сведения источников, скоринг с факторами, проверки, решения, предложения, журнал, вызовы систем, подсказки по ставкам |
| POST | `/api/v1/bank/applications/{id}/assign` | взять в работу |
| POST | `/api/v1/bank/applications/{id}/decision` | решение: `APPROVE` (варианты условий), `REQUEST_INFO` (что предоставить), `DECLINE` (причина) |
| POST | `/api/v1/bank/applications/{id}/rerun` | повторить автоматическую проверку (после сбоя источника) |
| GET | `/api/v1/bank/applications/{id}/documents/{doc_id}` | скачать документ клиента |
| GET / POST | `/api/v1/bank/applications/{id}/messages` | переписка с клиентом |
| GET | `/api/v1/bank/dashboard` | воронка, статусы, доля автоматических решений, время до решения, SLA, интеграции |
| GET | `/api/v1/bank/settings` | параметры политики |
| PUT | `/api/v1/bank/settings/{key}` | изменить ключевую ставку (риск-менеджер) |
| GET / POST | `/api/v1/bank/integrations/faults` | состояние и моделирование сбоев внешних систем |

Пример решения аналитика:

```json
POST /api/v1/bank/applications/{id}/decision
{
  "action": "APPROVE",
  "offers": [{"product_code": "INVESTMENT", "amount": 25000000, "term_months": 60, "rate": 16.5}],
  "message_to_client": "Одобрили инвестиционный кредит, залог — приобретаемый станок.",
  "comment_internal": "Финансовое положение устойчивое"
}
```

Если `rate` не указана, ставка считается по политике. Отклонение от политики сохраняется в расшифровке ставки как «корректировка кредитного специалиста».

### Служебные

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health/live` | процесс жив |
| GET | `/health/ready` | готовность (проверка БД) |
| GET | `/metrics` | метрики Prometheus; только напрямую с API (http://localhost:8000/metrics), nginx наружу их не отдаёт |

## Основной сценарий через curl

```bash
BASE=http://localhost:8080
TOKEN=$(curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"login":"smirnov","password":"demo1234"}' | jq -r .access_token)
AUTH="Authorization: Bearer $TOKEN"
COMPANY=$(curl -s $BASE/api/v1/auth/me -H "$AUTH" | jq -r '.companies[0].id')

# черновик
APP=$(curl -s $BASE/api/v1/applications -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"company_id\":\"$COMPANY\",\"product_code\":\"WORKING_CAPITAL\",\"purpose\":\"WORKING_CAPITAL\",\"amount\":3000000,\"term_months\":12}" | jq -r .id)

# согласия: код подписи (в демо-режиме возвращается в ответе)
CH=$(curl -s $BASE/api/v1/applications/$APP/sign-challenges -H "$AUTH" -H 'Content-Type: application/json' -d '{"purpose":"CONSENTS"}')
curl -s $BASE/api/v1/applications/$APP/consents -H "$AUTH" -H 'Content-Type: application/json' \
  -d "{\"challenge_id\":$(echo $CH | jq .challenge_id),\"code\":$(echo $CH | jq .demo_code)}" > /dev/null

# отправка и ожидание решения
curl -s -X POST $BASE/api/v1/applications/$APP/submit -H "$AUTH" > /dev/null
sleep 10
curl -s $BASE/api/v1/applications/$APP -H "$AUTH" | jq '{status, decision: .decision.outcome, offers: [.offers[] | {amount, term_months, rate, monthly_payment}]}'
```

## Контракты внешних систем (симулятор)

| Метод | Путь | Ответ |
|---|---|---|
| GET | `/egrul/v1/entities/{inn}` | статус, дата регистрации, руководитель (с датой назначения), учредители, ОКВЭД, адрес |
| GET | `/fns/v1/transparent-business/{inn}` | режим налогообложения, выручка, прибыль, налоговый долг, блокировки счетов, массовый адрес, недостоверность |
| POST | `/bki/v1/credit-reports` | `{"inn", "consent_id"}` → балл БКИ, длина истории, просрочки, запросы за 30 дней, кредиты с платежами |
| POST | `/abs/v1/loans` | `{"inn", "contract_number", "amount", "term_months", "rate", "settlement_account", "idempotency_key"}` → номер ссудного счёта; повтор с тем же ключом возвращает тот же результат |
| GET / POST | `/_control/faults` | `{"service": "BKI", "mode": "down" \| "slow" \| "ok", "duration_seconds": 120}` |

Симулятор добавляет задержку 150–600 мс к каждому ответу. В режиме `slow` ответ приходит через 8 секунд — дольше таймаута API (5 с), поэтому срабатывают повторы.
