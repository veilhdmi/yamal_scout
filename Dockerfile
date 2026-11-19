# Yamal Scout web app for Cloud Run.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./
COPY web ./web

# Run as an unprivileged user.
RUN useradd --create-home appuser
USER appuser

# Cloud Run sets $PORT. One worker: chat sessions and the rate limits live in this process's memory.
CMD ["sh", "-c", "exec uvicorn server:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
