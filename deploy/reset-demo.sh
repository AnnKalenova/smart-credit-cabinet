#!/usr/bin/env bash
# Возвращает демо-данные к исходным: заявки, кредиты и настройки политики — как после первого запуска.
# Сертификаты, домен и пароли не трогает. Можно поставить на ночь в cron (см. deploy/README.md).
set -euo pipefail
cd "$(dirname "$0")/.."
c() { bash deploy/compose.sh "$@"; }
c stop worker api
c exec -T db sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
c up -d # api при старте заново создаст таблицы и загрузит демо-данные
echo "Демо-данные сброшены: $(date '+%d.%m.%Y %H:%M')"
