# Веб-интерфейс: сборка React-приложения и раздача через nginx, который проксирует /api на backend.

FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
# обновления безопасности базового образа: в CI Trivy останавливает сборку на критических уязвимостях
# с доступным исправлением, а базовый образ пересобирается реже, чем выходят исправления (например, OpenSSL)
RUN apk upgrade --no-cache
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=6 \
    CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
