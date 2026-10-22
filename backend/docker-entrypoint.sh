#!/bin/sh
# Точка входа образа backend. Роль контейнера — первый аргумент:
#   api     — миграции БД, загрузка демо-данных (SEED_ON_START=true), запуск API на :8000
#   worker  — воркер кредитного конвейера (можно масштабировать: docker compose up --scale worker=3)
#   migrate — только миграции;  seed — только демо-данные;  иначе — выполнить переданную команду
set -e

role="${1:-api}"

case "$role" in
  api)
    alembic upgrade head
    if [ "${SEED_ON_START:-true}" = "true" ]; then
      python -m app.seed
    fi
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
      --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-*}"
    ;;
  worker)
    export WORKER_ID="${WORKER_ID:-$(hostname)}"
    exec python -m app.worker.runner
    ;;
  migrate)
    exec alembic upgrade head
    ;;
  seed)
    exec python -m app.seed
    ;;
  *)
    exec "$@"
    ;;
esac
