# Образ основного сервиса: API и воркер конвейера (роль задаётся аргументом: api | worker | migrate | seed).
# Контекст сборки — корень репозитория: в образ копируются синтетические данные из data/synthetic.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Зависимости ставятся строго по lock-файлу с проверкой хешей
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/uploads \
    && chown -R app:app /data

COPY backend/alembic.ini ./alembic.ini
COPY backend/migrations ./migrations
COPY backend/app ./app
COPY data/synthetic/companies.json ./data/synthetic/companies.json
COPY backend/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# убираем CR на случай, если репозиторий клонирован в Windows с autocrlf
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV SYNTHETIC_DATA_PATH=/app/data/synthetic/companies.json \
    UPLOAD_DIR=/data/uploads

USER app
EXPOSE 8000
VOLUME ["/data/uploads"]

HEALTHCHECK --interval=10s --timeout=5s --start-period=40s --retries=6 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=4).status == 200 else 1)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["api"]
