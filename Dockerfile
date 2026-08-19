FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMS_GATE_SERVER_HOST=0.0.0.0 \
    SMS_GATE_SERVER_PORT=8000 \
    SMS_GATE_LOCAL_API_ENABLED=1 \
    SMS_GATE_LOCAL_API_BASE_URL=http://127.0.0.1:18080 \
    SMS_GATE_LOCAL_API_USERNAME=sms

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
# commands.json is gitignored but imported at startup; fall back to the example
# so the image boots on a fresh clone.
RUN test -f config/commands.json || cp config/example_commands.json config/commands.json
RUN chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"

CMD ["python", "-m", "src.main"]
