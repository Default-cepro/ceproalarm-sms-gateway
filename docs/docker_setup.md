# Docker Setup (WSL/Linux)

Este flujo levanta el backend dentro de un contenedor. No mueve ADB al contenedor: ADB sigue corriendo en el host (WSL/Windows) y el backend consume la API local del telefono por `host.docker.internal`.

## 1) Preparar `.env`

Ejemplo minimo para correr en Docker:

```env
SMS_GATE_SERVER_PORT=8000
EXCEL_PATH=/app/data/devices.xlsx
```

Si ves error de permisos en logs, usa `/tmp` dentro del contenedor:

```env
SMS_GATE_LOG_PATH=/tmp/app.log
```

Si usas modo local ADB (SMS Gate Local server):

```env
SMS_GATE_LOCAL_API_ENABLED=1
SMS_GATE_LOCAL_API_BASE_URL=http://host.docker.internal:18080
SMS_GATE_LOCAL_API_USERNAME=sms
SMS_GATE_LOCAL_API_PASSWORD=<LOCAL_PASSWORD>
```

## 2) Levantar el contenedor

```bash
docker compose up --build
```

El servidor queda en `http://127.0.0.1:8000`.

Si prefieres `docker run`:

```bash
docker build -t ceproalarm-sms-gateway .
docker run --rm \
  -p 8000:8000 \
  --env-file .env \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/data:/app/data" \
  --add-host=host.docker.internal:host-gateway \
  ceproalarm-sms-gateway
```

## 3) ADB local (si aplica)

Ejecuta el script ADB en el host (WSL/Windows), **no** dentro del contenedor:

```bash
bash tools/setup_adb_local_webhooks.sh \
  --password "<LOCAL_SERVER_PASSWORD>" \
  --server-port 8000
```

Si necesitas usar el `adb.exe` de Windows desde WSL:

```bash
bash tools/setup_adb_local_webhooks.sh \
  --adb-bin "adb.exe" \
  --password "<LOCAL_SERVER_PASSWORD>" \
  --server-port 8000
```

## 4) Archivos Excel

- Usa `EXCEL_PATH=/app/data/archivo.xlsx`.
- Monta tus archivos en `./data` (ya está en `docker-compose.yml`).

## 5) Prueba rápida de webhook

```bash
bash tools/send_signed_test_event.sh \
  --url "http://127.0.0.1:8000/webhook/sms/events" \
  --signing-key "<SIGNING_KEY>"
```
