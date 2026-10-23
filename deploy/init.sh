#!/usr/bin/env bash
# Создаёт deploy/.env: спрашивает домен и почту, пароли генерирует сам.
set -euo pipefail
cd "$(dirname "$0")"
if [ -f .env ]; then
  echo "deploy/.env уже есть. Поменять настройки: nano deploy/.env"
  exit 0
fi
read -rp "Домен (например credit.example.ru): " domain
read -rp "Почта для Let's Encrypt: " email
domain="${domain#https://}"
domain="${domain%/}"
umask 077
cat > .env <<ENV
DOMAIN=$domain
ACME_EMAIL=$email
JWT_SECRET=$(openssl rand -hex 32)
POSTGRES_PASSWORD=$(openssl rand -hex 16)
CORS_ORIGINS=https://$domain
WEB_PORT=127.0.0.1:8080
DEMO_MODE=true
ENVIRONMENT=cloud
ENV
echo "Готово: deploy/.env. Запуск: bash deploy/compose.sh up -d --build"
