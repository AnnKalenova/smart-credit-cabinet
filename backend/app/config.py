from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация сервиса. Все значения переопределяются переменными окружения."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Умный кредитный кабинет"
    environment: str = "local"

    database_url: str = "postgresql+psycopg://credit:credit@localhost:5432/credit"
    db_pool_size: int = 10

    jwt_secret: str = "dev-only-secret-change-me-0123456789abcdef"
    jwt_ttl_minutes: int = 480

    # Внешние системы (в поставке — симулятор integrations-mock)
    integrations_base_url: str = "http://localhost:8001"
    integration_timeout_seconds: float = 5.0
    integration_max_attempts: int = 5
    integration_backoff_base_seconds: float = 2.0

    # Конвейер
    pipeline_stage_delay_seconds: float = 1.2  # пауза между этапами, чтобы в демо было видно движение
    worker_poll_interval_seconds: float = 0.5
    worker_id: str = "worker-1"

    synthetic_data_path: str = "../data/synthetic/companies.json"
    upload_dir: str = "/data/uploads"
    max_upload_mb: int = 10

    demo_mode: bool = True  # показывать демо-учётки и демо-коды подписи в интерфейсе
    seed_on_start: bool = True

    default_key_rate: float = 14.0
    offer_valid_days: int = 10
    manual_review_sla_hours: int = 4
    sign_code_ttl_seconds: int = 300

    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    log_level: str = "INFO"

    # Необязательный ИИ-помощник: любой OpenAI-совместимый провайдер с моделью с открытыми весами
    llm_enabled: bool = False
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
