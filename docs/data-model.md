# Модель данных

PostgreSQL 16. Схема создаётся миграциями Alembic (`backend/migrations`), ORM-модели — `backend/app/models.py`. Денежные суммы хранятся в `NUMERIC(18,2)`, время — в `timestamptz`, гибкие структуры (ответы источников, факторы скоринга, расчёты) — в `JSONB`.

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
    applications ||--o{ pipeline_jobs : "этапы конвейера"
    users ||--o{ notifications : "уведомления"

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
    }
    pipeline_jobs {
        bigint id PK
        uuid application_id FK
        string stage
        string status "PENDING / RUNNING / DONE / FAILED"
        int attempts
        timestamptz run_after
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
| `applications` | заявка: параметры, статус, подшаг, SLA, исполнитель | `status`, `stage_detail`, `review_due_at`, `version` |
| `application_documents` | файлы клиента (хранятся в томе `uploads`) | `doc_type`, `sha256`, `storage_key` |
| `consents` | согласия на запрос в БКИ и обработку данных | `consent_type`, `signed_by_id`, `signed_at`, `signature_ref` |
| `sign_challenges` | одноразовые коды простой ЭП (хранится только хеш) | `purpose`, `code_hash`, `expires_at`, `attempts` |
| `enrichment_results` | ответы источников, использованные в оценке (кеш на 24 ч) | `source` (EGRUL/FNS/BKI/INTERNAL), `payload` |
| `integration_calls` | журнал вызовов внешних систем | `service`, `status`, `duration_ms`, `attempt`, `correlation_id` |
| `check_results` | результаты проверки полноты по прогонам | `code`, `severity`, `run_id` |
| `scoring_results` | балл, рейтинг, PD, вклад факторов, метрики | `score`, `grade`, `factors`, `metrics`, `model_version` |
| `decisions` | все решения: автоматические и ручные, с причинами и расчётами | `kind`, `outcome`, `reasons`, `details`, `policy_version` |
| `offers` | варианты условий для клиента | `amount`, `term_months`, `rate`, `pricing`, `status`, `valid_until` |
| `info_requests` | запросы банка и ответы клиента внутри заявки | `items`, `status`, `answer_comment` |
| `messages` | переписка клиента и банка по заявке | `author_role`, `text` |
| `loans` | выданные кредиты с графиком платежей | `contract_number`, `loan_account`, `schedule` |
| `application_events` | журнал аудита всех действий | `event_type`, `from_status`, `to_status`, `actor_type`, `client_visible` |
| `notifications` | уведомления в кабинете (вместо SMS и почты) | `user_id`, `is_read` |
| `pipeline_jobs` | очередь этапов конвейера для воркеров | `stage`, `status`, `attempts`, `run_after`, `locked_by` |

## Решения, важные для банковской разработки

- **Оптимистичная блокировка** (`applications.version`). Если клиент и воркер одновременно меняют заявку, второй получит конфликт (HTTP 409 или повтор этапа), а не перезапишет данные.
- **Полный аудит.** Каждое изменение статуса и каждое действие пользователя попадает в `application_events`. Решения хранятся неизменяемыми записями с версией модели и политики (`model_version`, `policy_version`).
- **Хранение секретов.** Пароли — PBKDF2-SHA256 (210 000 итераций), коды подписи — только хеш с солью, токены не хранятся.
- **Персональные данные.** Хранятся только данные, нужные для решения, ссылки на согласия — в `consents`. В логах нет ПДн: только идентификаторы заявок и технические поля.
- **Очередь в той же БД.** Этап конвейера ставится в очередь в той же транзакции, что и смена статуса, поэтому этап не потеряется и не запустится для неподтверждённого статуса.
