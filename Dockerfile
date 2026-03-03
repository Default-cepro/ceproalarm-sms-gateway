FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SMS_GATE_SERVER_HOST=0.0.0.0 \
    SMS_GATE_SERVER_PORT=8000 \
    SMS_GATE_LOCAL_API_ENABLED=1 \
    SMS_GATE_LOCAL_API_BASE_URL=http://host.docker.internal:18080 \
    SMS_GATE_LOCAL_API_USERNAME=sms

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chown -R app:app /app

USER app

EXPOSE 8000

CMD ["python", "-m", "src.main"]
