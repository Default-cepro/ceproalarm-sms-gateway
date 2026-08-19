# Ceproalarm SMS Gateway

Pasarela SMS que audita una flota de rastreadores GPS y determina cuáles están en línea. Corre 24/7, programa rondas diarias de envío de SMS y escribe el resultado de vuelta en los archivos Excel de origen.

## Arquitectura real

El sistema **no** usa módem GSM ni puerto serial. El flujo real es:

1. Un servidor **FastAPI** (`python -m src.main`, uvicorn embebido) corre 24/7 y programa rondas diarias (`SMS_GATE_DAILY_RUN_TIMES`, zona horaria `SMS_GATE_TIMEZONE`).
2. En cada ronda envía un SMS `STATUS#`-style por dispositivo a través de la app **SMS Gateway** (https://sms-gate.app, docs: https://docs.sms-gate.app/getting-started/) corriendo en **modo Local server** en el teléfono.
3. El servidor habla con la API local del teléfono en `SMS_GATE_LOCAL_API_BASE_URL` (por defecto `http://127.0.0.1:18080`, puerto expuesto con `adb forward` en el host) y hace `POST /message` para enviar.
4. El teléfono devuelve los eventos SMS por webhook a `POST /webhook/sms/events` (vía `adb reverse` en el host). La firma HMAC es opcional (`SMS_GATE_REQUIRE_SIGNATURE`).
5. Un dispositivo que responde al menos una vez al día es **ONLINE**. Al cierre del día se escribe `Status`/`Error` de vuelta en los mismos archivos `.xlsx`, se envían alertas OFFLINE por SMS y se envía por correo el reporte con los Excel procesados adjuntos.

Los dispositivos se leen de `EXCEL_PATH` (archivo, carpeta o glob; p. ej. `data/lote_1/*.xlsx`). Los `.xlsx` son la fuente de verdad: se leen y escriben siempre vía `src/storage/excel.py` (pandas + openpyxl, edición de celdas en sitio para preservar el formato).

## Prerrequisitos

- **Python 3.11+**.
- **App SMS Gateway** en el teléfono, en **modo Local server** (con usuario/contraseña locales).
- **ADB** en el host (para `adb forward`/`adb reverse`). Nunca dentro de Docker.
- Para el reporte por correo: credenciales SMTP.

## Inicio rápido

```bash
# 1. Entorno virtual e instalación
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -r requirements-dev.txt   # solo para desarrollo/tests

# 2. Configuración de comandos (obligatorio; sin esto la app no arranca)
cp config/example_commands.json config/commands.json

# 3. Variables de entorno
cp .env.example .env
# edita .env con tus valores (ver tabla de variables)

# 4. Configura ADB en el host (forward/reverse)
bash tools/setup_adb_local_webhooks.sh --password "<LOCAL_SERVER_PASSWORD>" --server-port 8000
```

## Ejecución

**Servidor 24/7** (desde la raíz del repo; `.env` y `config/commands.json` se resuelven relativos a la raíz):

```bash
python -m src.main
```

**Lote único** (sin scheduler, para pruebas de humo):

```bash
SMS_GATE_SCHEDULE_ENABLED=0 python -m src.main
```

**Tests**:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Variables de entorno

Todas las variables se leen en `src/core/config.py` (fuente única de verdad). Ver `.env.example` para el plantilla con comentarios.

| Variable | Default | Descripción |
| --- | --- | --- |
| `SMS_GATE_SERVER_HOST` | `0.0.0.0` | Host donde escucha uvicorn. |
| `SMS_GATE_SERVER_PORT` | `8000` | Puerto donde escucha uvicorn. |
| `SMS_GATE_ACCESS_LOG` | `0` | Habilita el access log de uvicorn. |
| `SMS_GATE_AUTO_REGISTER_WEBHOOKS` | `0` | Registra webhooks en la Cloud API al arrancar (opcional). |
| `SMS_GATE_UNREGISTER_ON_EXIT` | `0` | Desregistra los webhooks Cloud al salir. |
| `SMS_GATE_API_URL` | `https://api.sms-gate.app/3rdparty/v1` | URL base de la Cloud API. |
| `SMS_GATE_API_USERNAME` | *(vacío)* | Usuario de la Cloud API (Home tab). |
| `SMS_GATE_API_PASSWORD` | *(vacío)* | Contraseña de la Cloud API. |
| `SMS_GATE_WEBHOOK_URL` | *(vacío)* | URL pública del webhook para registro Cloud. |
| `SMS_GATE_WEBHOOK_EVENTS` | `sms:received,sms:sent,sms:delivered,sms:failed` | Eventos a registrar. |
| `SMS_GATE_DEVICE_ID` | *(vacío)* | ID de dispositivo para el registro Cloud (vacío = todos). |
| `SMS_GATE_WEBHOOK_SIGNING_KEY` | *(vacío)* | Clave para verificar la firma HMAC de los webhooks. |
| `SMS_GATE_REQUIRE_SIGNATURE` | `0` | Exige firma HMAC en los webhooks entrantes. |
| `SMS_GATE_TIMESTAMP_TOLERANCE_SECONDS` | `300` | Tolerancia de antigüedad del timestamp en la firma. |
| `SMS_GATE_MAX_TRACKED_DELIVERIES` | `5000` | Máximo de entregas rastreadas en memoria. |
| `SMS_GATE_LOCAL_API_ENABLED` | `0` | Habilita el envío vía la API local del teléfono. |
| `SMS_GATE_LOCAL_API_BASE_URL` | `http://127.0.0.1:18080` | URL base de la API local (modo Local server). |
| `SMS_GATE_LOCAL_API_USERNAME` | `sms` | Usuario de la API local. |
| `SMS_GATE_LOCAL_API_PASSWORD` | *(vacío)* | Contraseña de la API local. |
| `SMS_GATE_LOCAL_API_COUNTRY_CODE` | `58` | Código de país para formatear los teléfonos salientes. |
| `EXCEL_PATH` | *(vacío)* | Archivo, carpeta o glob de los `.xlsx` a procesar. |
| `SMS_GATE_SMS_RETRIES` | `1` | Reintentos por envío. |
| `SMS_GATE_SMS_RETRY_DELAY_SECONDS` | `30` | Espera entre reintentos. |
| `SMS_GATE_SMS_TIMEOUT_SECONDS` | `30` | Timeout de espera de respuesta. |
| `SMS_GATE_SCHEDULE_ENABLED` | `1` | Habilita el scheduler 24/7 (0 = lote único). |
| `SMS_GATE_DAILY_RUN_TIMES` | `08:00,14:00,20:00` | Horas de las rondas diarias. |
| `SMS_GATE_SKIP_PAST_ROUNDS` | `1` | Omite rondas ya vencidas al arrancar. |
| `SMS_GATE_SKIP_GRACE_SECONDS` | `60` | Ventana de gracia para considerar una ronda vencida. |
| `SMS_GATE_TIMEZONE` | *(vacío)* | Zona horaria del scheduler (p. ej. `America/Caracas`). |
| `SMS_GATE_MAINTENANCE_FLAG_PATH` | `data/maintenance.pause` | Ruta del archivo bandera de mantenimiento. |
| `SMS_GATE_MAINTENANCE_RECHECK_SECONDS` | `60` | Intervalo de rechequeo de la bandera de mantenimiento. |
| `SMS_GATE_PERSISTENCE_ENABLED` | `1` | Habilita la persistencia de estado. |
| `SMS_GATE_PERSISTENCE_PATH` | `data/run_state.json` | Archivo de estado de ejecución. |
| `SMS_GATE_OFFLINE_ALERT_RECIPIENTS` | `04143417356` | Teléfonos que reciben las alertas OFFLINE. |
| `EMAIL_REPORT_ENABLED` | `0` | Habilita el reporte por correo al cierre del día. |
| `EMAIL_REPORT_RECIPIENTS` | *(vacío)* | Destinatarios del reporte. |
| `EMAIL_REPORT_SUBJECT_PREFIX` | `Ceproalarm SMS Gateway` | Prefijo del asunto del correo. |
| `EMAIL_SMTP_HOST` | *(vacío)* | Host SMTP. |
| `EMAIL_SMTP_PORT` | `587` | Puerto SMTP. |
| `EMAIL_SMTP_USERNAME` | *(vacío)* | Usuario SMTP. |
| `EMAIL_SMTP_PASSWORD` | *(vacío)* | Contraseña SMTP. |
| `EMAIL_FROM` | *(vacío, usa `EMAIL_SMTP_USERNAME`)* | Remitente del correo. |
| `EMAIL_SMTP_USE_SSL` | `0` | Usa SSL para SMTP. |
| `EMAIL_SMTP_USE_TLS` | `1` | Usa TLS para SMTP. |
| `EMAIL_SMTP_TIMEOUT_SECONDS` | `20` | Timeout de SMTP. |
| `SMS_GATE_LOG_PATH` | *(vacío, `logs/app.log`)* | Ruta del archivo de log. |

## Operación y mantenimiento

- **Pausar el scheduler sin apagar el API**: crea el archivo bandera `touch data/maintenance.pause`; el API/webhook sigue activo. Retíralo (`rm -f data/maintenance.pause`) para reanudar.
- **Logs**: por defecto en `logs/app.log` (rotación de 1 MB) y en stdout. Configurable con `SMS_GATE_LOG_PATH`.
- **Persistencia**: el estado de ejecución vive en `data/run_state.json` (esquema v2, escrituras atómicas con copia `.bak`). Permite reanudar una ronda interrumpida.
- **Excel**: los `.xlsx` en `data/lote_1/` son la fuente de verdad y contienen datos reales de producción. Haz backup antes de tocar el flujo.

## Docker (servidor Linux)

El contenedor apunta al servidor Linux únicamente: `network_mode: host`, ADB siempre en el host, UID/GID del host, healthcheck en `/` y `restart: unless-stopped`. Ver [docs/docker_setup.md](docs/docker_setup.md).

## Estructura del proyecto

```
src/
  api/        FastAPI (app, webhooks, estado en memoria)
  core/       Configuración, comandos, teléfonos, parser, validador, logger
  services/   Scheduler, ciclo de día, envío SMS, persistencia, correo, workers
  storage/    Lectura/escritura de los .xlsx (única vía permitida)
tests/        Suite pytest
tools/        Scripts de ADB y prueba de webhooks
docs/         Documentación (Docker, ADB, webhooks)
```

## Licencia

MIT. Ver [LICENSE](LICENSE).
