# Webhooks SMS Gate: Setup y prueba en tiempo real

## 1) Configurar variables de entorno

Copia `.env.example` a `.env` y ajusta la llave:

```env
SMS_GATE_WEBHOOK_SIGNING_KEY=1dc5123a628aeefd
SMS_GATE_REQUIRE_SIGNATURE=1
SMS_GATE_TIMESTAMP_TOLERANCE_SECONDS=300
SMS_GATE_MAX_TRACKED_DELIVERIES=5000
```

Si quieres auto registro en startup de `src.main`:

```env
SMS_GATE_AUTO_REGISTER_WEBHOOKS=1
SMS_GATE_API_URL=https://api.sms-gate.app/3rdparty/v1
SMS_GATE_API_USERNAME=<CLOUD_USERNAME_HOME_TAB>
SMS_GATE_API_PASSWORD=<CLOUD_PASSWORD_HOME_TAB>
SMS_GATE_WEBHOOK_URL=https://<TU_NGROK>/webhook/sms/events
SMS_GATE_WEBHOOK_EVENTS=sms:received,sms:sent,sms:delivered,sms:failed
SMS_GATE_DEVICE_ID=
SMS_GATE_UNREGISTER_ON_EXIT=0
```

`/webhook/sms/device` (`login`, `deviceName`) no son credenciales de Cloud API.

## 2) Arrancar el servidor

```powershell
python -m src.main
```

## 3) Registrar webhooks (Cloud mode)

### Opción A: Script PowerShell

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\register_smsgate_webhooks.ps1 `
  -Username "<USERNAME_APP>" `
  -Password "<PASSWORD_APP>" `
  -WebhookUrl "https://<TU_NGROK>/webhook/sms/events"
```

### Opción B: curl manual (uno por evento)

```bash
curl -X POST -u <username>:<password> \
  -H "Content-Type: application/json" \
  -d '{ "url": "https://<TU_NGROK>/webhook/sms/events", "event": "sms:received" }' \
  https://api.sms-gate.app/3rdparty/v1/webhooks
```

Repite para `sms:sent`, `sms:delivered`, `sms:failed`.

## 4) Probar firma y recepción sin esperar al tracker

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\send_signed_test_event.ps1 `
  -Url "http://127.0.0.1:80/webhook/sms/events" `
  -SigningKey "1dc5123a628aeefd" `
  -PhoneNumber "4243616194" `
  -Message "STATUS,0000#"
```

## 5) Qué validar

- El endpoint responde `200`.
- En logs aparece `INCOMING SMS GATE EVENT event=sms:received`.
- Si hay comando pendiente al mismo número, debe resolverse el `send_command_and_wait`.

## Notas de modo

- Tu app ya usa endpoints legacy (`/webhook/sms/message`, `/webhook/sms/device`, etc.) para polling y envío.
- Los eventos webhook nuevos (`/webhook/sms/events`) no rompen ese flujo; funcionan en paralelo para recepción en tiempo real.
