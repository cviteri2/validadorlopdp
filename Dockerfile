FROM python:3.11-slim

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lopdp_validator.py checklist_lopdp.json ./
COPY assets ./assets
COPY web ./web

USER appuser

ENV MAX_FILE_MB=8 \
    RATE_LIMIT_PER_HOUR=5

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')" || exit 1

CMD ["gunicorn", "web.app:app", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "30"]
