# Модель данных

PostgreSQL 16. Схема создаётся миграциями Alembic (`backend/migrations`: `0001` — начальная схема, `0002` — попытки входа, `0003` — уникальность согласий, дата подписания договора (у подписанных раньше — из журнала событий), индекс попыток входа по адресу), ORM-модели — `backend/app/models.py`. Денежные суммы хранятся в `NUMERIC(18,2)`, время — в `timestamptz`, гибкие структуры (ответы источников, факторы скоринга, расчёты) — в `JSONB`.

![Модель данных](diagrams/data-model.png)

```mermaid
erDiagram
    users ||--o{ company_members : "состоит в"
    companies ||--o{ company_members : "имеет представителей"
    companies ||--o{ account_turnovers : "обороты по счёту"
    companies ||--o{ applications : "подаёт"
    products ||--o{ applications : "продукт"
    users ||--o{ applications : "создал / ведёт"
    applications ||--o{ application_documents : "документы"
    applications ||--o{ consents : "согласия"
    applications ||--o{ sign_challenges : "коды подписи"
    applications ||--o{ enrichment_results : "сведения источников"
    applications ||--o{ integration_calls : "вызовы систем"
    applications ||--o{ check_results : "проверки"
    applications ||--o{ scoring_results : "скоринг"
    applications ||--o{ decisions : "решения"
    decisions ||--o{ offers : "предложения"
    applications ||--o{ info_requests : "запросы банка"
    applications ||--o{ messages : "переписка"
    applications ||--o| loans : "кредит"
    applications ||--o{ application_events : "журнал"
    applications |o--o{ applications : "повторная подача"
    applications ||--o{ pipeline_jobs : "этапы конвейера"
    users ||--o{ notifications : "уведомления"
    users ||..o{ login_attempts : "попытки входа"

    companies {
        uuid id PK
        string inn UK
        string ogrn
        string kind "LEGAL / IP"
        string short_name
        string okved_code
        date registration_date
        date client_since
        string settlement_account
    }
    users {
        uuid id PK
        string login UK
        string password_hash "PBKDF2-SHA256"
        string role "CLIENT / ANALYST / RISK_MANAGER"
    }
    company_members {
        uuid company_id FK
        uuid user_id FK
        string position
        bool can_sign
    }
    applications {
        uuid id PK
        string number UK
        uuid company_id FK
        string product_code FK
        numeric amount
        int term_months
        string purpose
        numeric declared_revenue
        string status
        string stage_detail
        uuid assignee_id FK
        uuid parent_id FK "исходная заявка при повторной подаче"
        int info_rounds
        int version "оптимистичная блокировка"
    }
    decisions {
        uuid id PK
        uuid application_id FK
        string kind "AUTO / MANUAL"
        string outcome
        jsonb reasons
        jsonb details "лимит, ставка, рекомендация"
        uuid decided_by_id FK
    }
    offers {
        uuid id PK
        uuid decision_id FK
        string product_code
        numeric amount
        int term_months
        numeric rate
        numeric monthly_payment
        jsonb pricing
        string status
        timestamptz valid_until
        timestamptz signed_at "дата подписания: от неё строится график"
    }
    consents {
        uuid id PK
        uuid application_id FK "уникально в паре с consent_type"
        string consent_type "BKI_REQUEST / PERSONAL_DATA"
        uuid signed_by_id FK
        timestamptz signed_at
    }
    application_documents {
        uuid id PK
        uuid application_id FK
        string doc_type
        string sha256
        string storage_key "общий у документов, перенесённых в новую заявку"
    }
    pipeline_jobs {
        bigint id PK
        uuid application_id FK
        string stage
        string status "PENDING / RUNNING / DONE / FAILED"
        int attempts
        timestamptz run_after
    }
    login_attempts {
        bigint id PK
        string login "без FK: учитываются и несуществующие логины"
        bool success
        string ip "индекс (ip, created_at)"
        timestamptz created_at
    }
```

## Таблицы

| Таблица | Назначение | Важные поля |
|---|---|---|
| `users` | пользователи: представители клиентов и сотрудники банка | `role`, `password_hash` |
| `companies` | клиенты банка (ИП и юрлица), данные уже известны банку | `inn`, `ogrn`, `kind`, `client_since`, `settlement_account` |
| `company_members` | кто представляет компанию и может ли подписывать | `position`, `can_sign` |
| `account_turnovers` | внутренние данные банка: обороты по счёту помесячно | `credit_turnover`, `debit_turnover`, `avg_balance` |
| `products` | каталог продуктов и их параметры (ставки, лимиты, правила документов) | `base_spread`, `max_spread`, `turnover_multiplier`, `auto_decision_limit`, `doc_rules` |
| `settings` | параметры политики, которые меняются без релиза | `key_rate`, `key_rate_note` |
| `applications` | заявка: параметры, статус, подшаг, SLA, исполнитель, ссылка на исходную заявку | `status`, `stage_detail`, `review_due_at`, `parent_id`, `version` |
| `application_documents` | файлы клиента (хранятся в томе `uploads`). Документы, перенесённые в новую заявку, ссылаются на тот же файл: `storage_key` у них общий, файл удаляется, когда на него не ссылается ни одна заявка | `doc_type`, `sha256`, `storage_key` |
| `consents` | согласия на запрос в БКИ и обработку данных; одно согласие каждого вида на заявку (уникальность `application_id` + `consent_type`) | `consent_type`, `signed_by_id`, `signed_at`, `signature_ref` |
| `sign_challenges` | одноразовые коды простой ЭП (хранится только хеш) | `purpose`, `code_hash`, `expires_at`, `attempts` |
| `enrichment_results` | ответы источников, использованные в оценке (кеш на 24 ч) | `source` (EGRUL/FNS/BKI/INTERNAL), `payload` |
| `integration_calls` | журнал вызовов внешних систем | `service`, `status`, `duration_ms`, `attempt`, `correlation_id` |
| `check_results` | результаты проверки полноты по прогонам | `code`, `severity`, `run_id` |
| `scoring_results` | балл, рейтинг, PD, вклад факторов, метрики | `score`, `grade`, `factors`, `metrics`, `model_version` |
| `decisions` | все решения: автоматические и ручные, с причинами и расчётами | `kind`, `outcome`, `reasons`, `details`, `policy_version` |
| `offers` | варианты условий для клиента; у подписанного — дата подписания, от неё строится график, поэтому подписанный график и график выданного кредита совпадают | `amount`, `term_months`, `rate`, `pricing`, `status`, `valid_until`, `signed_at` |
| `info_requests` | запросы банка и ответы клиента внутри заявки | `items`, `status`, `answer_comment` |
| `messages` | переписка клиента и банка по заявке | `author_role`, `text` |
| `loans` | выданные кредиты с графиком платежей | `contract_number`, `loan_account`, `schedule` |
| `application_events` | журнал аудита всех действий; в том числе условия, которые сотрудник без права подписи передал руководителю (`CONDITIONS_PROPOSED`, условия — в `payload`) | `event_type`, `from_status`, `to_status`, `actor_type`, `client_visible`, `payload` |
| `notifications` | уведомления в кабинете (вместо SMS и почты) | `user_id`, `is_read` |
| `login_attempts` | попытки входа: защита от подбора пароля и журнал входов; индексы по логину и по адресу | `login`, `success`, `ip`, `created_at` |
| `pipeline_jobs` | очередь этапов конвейера для воркеров | `stage`, `status`, `attempts`, `run_after`, `locked_by` |

## Решения, важные для банковской разработки

- **Оптимистичная блокировка** (`applications.version`). Если клиент и воркер одновременно меняют заявку, второй получит конфликт (HTTP 409 или повтор этапа), а не перезапишет данные.
- **Аудит.** Смены статуса, подписи, документы, запросы и решения аналитика попадают в `application_events` с автором и временем, вызовы внешних систем — в `integration_calls`, попытки входа — в `login_attempts`. Решения хранятся неизменяемыми записями с версией модели и политики (`model_version`, `policy_version`).
- **Хранение секретов.** Пароли — PBKDF2-SHA256 (210 000 итераций), коды подписи — только хеш с солью, токены не хранятся.
- **Персональные данные.** Хранятся только данные, нужные для решения, ссылки на согласия — в `consents`. В логах нет ПДн: только идентификаторы заявок и технические поля.
- **Защита от гонок.** Действия, где важен лимит или единственность, выполняются под транзакционной advisory-блокировкой PostgreSQL по ключу заявки: подпись согласий и договора, выдача кодов, загрузка документов (не больше 20 на заявку), повторная подача, повтор этапа. Две одновременные кнопки не создадут две заявки и две подписи.
- **Очередь в той же БД.** Этап конвейера ставится в очередь в той же транзакции, что и смена статуса, поэтому этап не потеряется и не запустится для неподтверждённого статуса.
