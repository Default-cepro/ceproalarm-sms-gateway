# ADB Local Mode Setup (sin Cloud)

Este flujo evita depender de la red local del telefono y usa tuneles ADB:

- `adb forward` para que tu PC consuma la API local del telefono.
- `adb reverse` para que el telefono llame tu servidor FastAPI en `localhost`.

## Prerrequisitos

- `adb` instalado y el dispositivo visible en `adb devices -l`.
- App SMS Gateway en **Local server** activa.
- Credenciales de Local server visibles en la app:
  - `Username` (ej. `sms`)
  - `Password`
  - `Device ID` (opcional, para depuracion)

## Comando unico de configuracion

### Windows (PowerShell)

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\setup_adb_local_webhooks.ps1 `
  -AdbSerial "JBV489BM5PCAE6EM" `
  -Username "sms" `
  -Password "<LOCAL_SERVER_PASSWORD>" `
  -ServerPort 80
```

### WSL / Linux (bash)

```bash
bash tools/setup_adb_local_webhooks.sh \
  --adb-serial "JBV489BM5PCAE6EM" \
  --username "sms" \
  --password "<LOCAL_SERVER_PASSWORD>" \
  --server-port 8000
```

Si necesitas usar el `adb.exe` de Windows desde WSL:

```bash
bash tools/setup_adb_local_webhooks.sh \
  --adb-bin "adb.exe" \
  --password "<LOCAL_SERVER_PASSWORD>"
```

Parametros opcionales:

- `-AdbSerial "JBV489BM5PCAE6EM"` si tienes varios dispositivos.
- `-ServerPort` puerto donde corre tu FastAPI local.
- `-ReversePort` default `9876` (telefono -> PC).
- `-ForwardPort` default `18080` (PC -> telefono).


## Que hace el script

1. Verifica `adb` y dispositivo.
2. Configura:
   - `adb forward tcp:18080 tcp:8080`
   - `adb reverse tcp:9876 tcp:80` (si `-ServerPort 80`)
3. Detecta endpoint de webhooks del telefono:
   - `http://127.0.0.1:18080/webhooks` o
   - `http://127.0.0.1:18080/3rdparty/v1/webhooks`
4. Registra eventos:
   - `sms:received`, `sms:sent`, `sms:delivered`, `sms:failed`
5. Consulta lista de webhooks registrados.

Webhook registrado por el telefono:

`http://127.0.0.1:9876/webhook/sms/events`

## Verificacion rapida

1. Arranca backend:
   - En `.env` habilita:
     - `SMS_GATE_LOCAL_API_ENABLED=1`
     - `SMS_GATE_LOCAL_API_BASE_URL=http://127.0.0.1:18080`
     - `SMS_GATE_LOCAL_API_USERNAME=sms`
     - `SMS_GATE_LOCAL_API_PASSWORD=<LOCAL_PASSWORD>`
     - `SMS_GATE_SERVER_PORT=8000` (si no usas 80)
   - `python -m src.main`
2. Ejecuta script ADB de arriba.
3. Envia un SMS de prueba al telefono o desde la app.
4. Verifica logs del backend:
   - `INCOMING SMS GATE EVENT event=sms:received`

## Si falla

- Si `adb forward/reverse` falla, revisa USB debugging autorizado.
- Si registro devuelve `401`, username/password local no coinciden.
- Si no llega webhook, verifica que FastAPI este levantado en `localhost:<ServerPort>`.
