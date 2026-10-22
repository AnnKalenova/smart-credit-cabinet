# Симулятор внешних систем (ЕГРЮЛ/ЕГРИП, ФНС, БКИ, АБС). Контекст сборки — корень репозитория.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY integrations-mock/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

RUN groupadd --system mock \
    && useradd --system --gid mock --home-dir /srv --no-create-home --shell /usr/sbin/nologin mock

COPY integrations-mock/app ./app
COPY data/synthetic/companies.json ./data/synthetic/companies.json

ENV SYNTHETIC_DATA_PATH=/srv/data/synthetic/companies.json

USER mock
EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8001/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
