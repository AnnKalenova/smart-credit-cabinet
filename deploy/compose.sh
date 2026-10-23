#!/usr/bin/env bash
# docker compose с настройками публикации: ./deploy/compose.sh up -d --build | ps | logs -f caddy | down
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -f deploy/.env ]; then
  echo "Нет deploy/.env — сначала запустите ./deploy/init.sh" >&2
  exit 1
fi
exec docker compose -f docker-compose.yml -f deploy/docker-compose.prod.yml --env-file deploy/.env "$@"
