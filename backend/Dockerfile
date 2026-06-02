FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY backend/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

# Бэкенд + сам дашборд (index.html лежит в корне репозитория).
COPY backend/ ./
COPY index.html /index.html

# Cloud Run передаёт порт через переменную PORT.
ENV PORT=8080
CMD exec uvicorn app:app --host 0.0.0.0 --port ${PORT}
