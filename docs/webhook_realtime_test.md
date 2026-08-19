# Webhooks SMS Gate: Setup y prueba en tiempo real

## 1) Configurar variables de entorno

Copia `.env.example` a `.env` y ajusta la llave:

```env
SMS_GATE_WEBHOOK_SIGNING_KEY=1dc5123a628aeefd
SMS_GATE_REQUIRE_SIGNATURE=1
SMS_GATE_TIMESTAMP_TOLERANCE_SECONDS=300
SMS_GATE_MAX_TRACKED_DELIVERIES=5000
```

### Modo local / ADB (sin firma)

En el flujo local (app SMS Gateway en modo Local server con `adb reverse`), los eventos entrantes llegan **sin firma**. Deja la verificación desactivada:

```env
SMS_GATE_REQUIRE_SIGNATURE=0
```

Con `SMS_GATE_REQUIRE_SIGNATURE=0` el servidor acepta webhooks sin cabeceras `X-Signature`/`X-Timestamp`. Si la activas (`=1`), exige la firma HMAC-SHA256 de `raw_body + x-timestamp` en la cabecera `x-signature`, con tolerancia de `SMS_GATE_TIMESTAMP_TOLERANCE_SECONDS`.

### Auto registro en Cloud (opcional)

Si quieres auto registro en startup de `src.main` (vía `src/services/webhook_registry.py`):

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

> Las credenciales de Cloud API son las del **Home tab** de la app, no las del flujo local API.

## 2) Arrancar el servidor

```bash
python -m src.main
```

## 3) Registrar webhooks (Cloud mode)

### Opción A: Script bash

```bash
bash tools/register_smsgate_webhooks.sh \
  --username "<USERNAME_APP>" \
  --password "<PASSWORD_APP>" \
  --webhook-url "https://<TU_NGROK>/webhook/sms/events"
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

```bash
bash tools/send_signed_test_event.sh \
  --url "http://127.0.0.1:8000/webhook/sms/events" \
  --signing-key "1dc5123a628aeefd" \
  --phone-number "4243616194" \
  --message "STATUS,0000#"
```

> En modo local/ADB (`SMS_GATE_REQUIRE_SIGNATURE=0`) puedes probar con un `curl` sin firmar:
>
> ```bash
> curl -X POST http://127.0.0.1:8000/webhook/sms/events \
>   -H "Content-Type: application/json" \
>   -d '{"event":"sms:received","payload":{"phoneNumber":"4243616194","message":"STATUS,0000#"}}'
> ```

## 5) Qué validar

- El endpoint responde `200`.
- En logs aparece `INCOMING SMS GATE EVENT event=sms:received`.
- Si hay comando pendiente al mismo número, debe resolverse el `send_command_and_wait`.
