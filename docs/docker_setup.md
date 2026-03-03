# Docker Setup (WSL/Linux)

Este flujo levanta el backend dentro de un contenedor. No mueve ADB al contenedor: ADB sigue corriendo en el host (WSL/Windows) y el backend consume la API local del telefono por `host.docker.internal`.

`docker-compose.yml` ya fuerza:
- `SMS_GATE_LOCAL_API_ENABLED=1`
- `SMS_GATE_LOCAL_API_BASE_URL=http://host.docker.internal:18080`
- ejecución con UID/GID del host (`${UID:-1000}:${GID:-1000}`) para evitar errores de permisos al escribir en `./data` y `./logs`.

## 1) Preparar `.env`

Ejemplo minimo para correr en Docker:

```env
SMS_GATE_SERVER_PORT=8000
EXCEL_PATH=/app/data/devices.xlsx
SMS_GATE_TIMEZONE=America/Caracas
SMS_GATE_ACCESS_LOG=0
```

Si ves error de permisos en logs, usa `/tmp` dentro del contenedor:

```env
SMS_GATE_LOG_PATH=/tmp/app.log
```

Si usas modo local ADB (SMS Gate Local server):

```env
SMS_GATE_LOCAL_API_ENABLED=1
SMS_GATE_LOCAL_API_USERNAME=sms
SMS_GATE_LOCAL_API_PASSWORD=<LOCAL_PASSWORD>
```

Scheduler 24/7 (rondas diarias por horario):

```env
SMS_GATE_SCHEDULE_ENABLED=1
SMS_GATE_DAILY_RUN_TIMES=08:00,14:00,20:00
SMS_GATE_SKIP_PAST_ROUNDS=1
SMS_GATE_MAINTENANCE_FLAG_PATH=/app/data/maintenance.pause
SMS_GATE_MAINTENANCE_RECHECK_SECONDS=60
```

Alerta de OFFLINE al cierre del día:

```env
SMS_GATE_OFFLINE_ALERT_RECIPIENTS=04143417356
```

## 2) Levantar el contenedor

```bash
docker compose up --build
```

Si tu usuario en WSL no es UID/GID 1000, exporta antes:

```bash
export UID="$(id -u)"
export GID="$(id -g)"
docker compose up --build
```

El servidor queda en `http://127.0.0.1:8000`.

Si prefieres `docker run`:

```bash
docker build -t ceproalarm-sms-gateway .
docker run --rm \
  --name ceproalarm-sms-gateway \
  --user "$(id -u):$(id -g)" \
  -p 8000:8000 \
  --env-file .env \
  -e SMS_GATE_LOCAL_API_ENABLED=1 \
  -e SMS_GATE_LOCAL_API_BASE_URL=http://host.docker.internal:18080 \
  -v "$(pwd)/logs:/app/logs" \
  -v "$(pwd)/data:/app/data" \
  --add-host=host.docker.internal:host-gateway \
  ceproalarm-sms-gateway
```

`docker run --rm --name <name> <image>` por si solo no carga `.env`, no monta volúmenes y no agrega `host.docker.internal`, por eso suele quedarse en timeout o fallar al enviar/guardar.

Si levantas el contenedor desde Docker Desktop (boton Run), agrega esas mismas variables de entorno manualmente. Si no las agregas, el servicio entra en:
`Esperando primer llamado de la app (timeout=300s)`.

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

## 6) Mantenimiento sin apagar el server

Para pausar solo las rondas (el API/webhook sigue activo), crea el archivo bandera:

```bash
touch data/maintenance.pause
```

Cuando termines mantenimiento, retíralo y el scheduler reanuda automáticamente:

```bash
rm -f data/maintenance.pause
```

Casos típicos de mantenimiento:
- Rotación/backup de Excel (`./data`).
- Revisión de logs y espacio en disco (`./logs`).
- Ajuste de horarios (`SMS_GATE_DAILY_RUN_TIMES`) y reinicio controlado del contenedor.
