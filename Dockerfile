FROM python:3.12.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    DATABASE_PATH=/data/market.sqlite3 \
    UPLOAD_DIR=/data/uploads

WORKDIR /app

RUN addgroup --system marketplace && adduser --system --ingroup marketplace marketplace

COPY requirements.txt ./
RUN pip install --requirement requirements.txt

COPY app ./app
RUN mkdir -p /data/uploads && chown -R marketplace:marketplace /data

USER marketplace
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header", "--ws-max-size", "4096", "--ws-max-queue", "16", "--ws-per-message-deflate", "false", "--limit-concurrency", "100", "--proxy-headers", "--forwarded-allow-ips=127.0.0.1"]
