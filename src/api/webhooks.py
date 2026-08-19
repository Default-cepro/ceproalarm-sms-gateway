import hashlib
import hmac
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..core.config import settings
from ..core.phones import phones_equivalent
from .state import (
    _handle_incoming_and_try_match,
    _is_quiet_message_id,
    _remember_delivery,
    _remember_incoming_message,
    _remember_status_event,
    incoming_sms_queue,
    message_statuses,
    pending_commands,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def parse_body_bytes(raw: bytes, content_type: str) -> Any:
    try:
        s = raw.decode("utf-8") if raw else ""
    except Exception:
        return {}
    if "application/json" in (content_type or ""):
        try:
            return json.loads(s or "{}")
        except Exception:
            return {}
    if "application/x-www-form-urlencoded" in (content_type or "") or "multipart/form-data" in (content_type or ""):
        try:
            parsed_qs = parse_qs(s or "")
            return {k: v[0] for k, v in parsed_qs.items()}
        except Exception:
            return {}
    try:
        return json.loads(s or "{}")
    except Exception:
        try:
            parsed_qs = parse_qs(s or "")
            return {k: v[0] for k, v in parsed_qs.items()}
        except Exception:
            return {}


def success_payload(extra: Optional[dict] = None) -> dict:
    base = {"payload": {"success": True, "error": None}}
    if extra:
        base.update(extra)
        if isinstance(extra.get("payload"), dict):
            base["payload"].update(extra["payload"])
    return base


def _is_sms_gate_event(parsed: Any) -> bool:
    return (
        isinstance(parsed, dict)
        and isinstance(parsed.get("event"), str)
        and isinstance(parsed.get("payload"), dict)
    )


def _verify_sms_gate_signature(raw_body: bytes, request: Request) -> Optional[str]:
    signature = (request.headers.get("x-signature") or "").strip()
    timestamp = (request.headers.get("x-timestamp") or "").strip()

    if not settings.webhook_signing_key:
        if settings.require_signature:
            return "SMS_GATE_WEBHOOK_SIGNING_KEY is not configured on server"
        return None

    # In local/ADB mode, incoming events can be unsigned. Only require headers
    # when signature enforcement is explicitly enabled.
    if not signature and not timestamp:
        if settings.require_signature:
            return "missing X-Signature or X-Timestamp header"
        return None

    if not signature or not timestamp:
        return "missing X-Signature or X-Timestamp header"

    try:
        timestamp_int = int(timestamp)
    except Exception:
        return "invalid X-Timestamp header"

    now = int(time.time())
    if abs(now - timestamp_int) > settings.timestamp_tolerance_seconds:
        return "timestamp out of accepted range"

    mac = hmac.new(settings.webhook_signing_key.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(raw_body)
    mac.update(timestamp.encode("utf-8"))
    expected_signature = mac.hexdigest()

    if not hmac.compare_digest(expected_signature, signature.lower()):
        return "invalid signature"

    return None


async def _store_and_match_incoming(phone: Optional[str], message: Optional[str], parsed: Dict[str, Any]):
    await incoming_sms_queue.put({"phone": phone, "message": message, "raw": parsed})

    try:
        await _handle_incoming_and_try_match(parsed)
    except Exception as ex:
        logging.exception("Error matching incoming SMS: %s", ex)


def _update_status_from_sms_gate_event(
    event_name: str,
    payload: Dict[str, Any],
    envelope: Dict[str, Any],
    quiet: bool = False,
):
    message_id = payload.get("messageId") or envelope.get("id") or str(uuid.uuid4())
    phone = payload.get("phoneNumber")
    reason = payload.get("reason")
    now_ts = int(time.time())

    current = message_statuses.get(message_id, {})
    history = current.get("events", [])
    if not isinstance(history, list):
        history = []
    history.append({
        "event": event_name,
        "phoneNumber": phone,
        "reason": reason,
        "received_at": now_ts
    })
    if len(history) > 20:
        history = history[-20:]

    state_map = {
        "sms:sent": "sent",
        "sms:delivered": "delivered",
        "sms:failed": "failed"
    }
    state = state_map.get(event_name, event_name)

    message_statuses[message_id] = {
        "id": message_id,
        "state": state,
        "phoneNumber": phone,
        "reason": reason,
        "updated_at": now_ts,
        "events": history,
        "raw": envelope
    }

    if state == "failed":
        if quiet:
            logging.warning("sms:failed (quiet msg) for %s (message_id=%s): %s", phone, message_id, reason or "unknown")
        else:
            logging.error("sms:failed for %s (message_id=%s): %s", phone, message_id, reason or "unknown")
    else:
        if quiet:
            logging.debug("Updated status (quiet msg) from %s for %s (message_id=%s)", event_name, phone, message_id)
        else:
            logging.info("Updated status from %s for %s (message_id=%s)", event_name, phone, message_id)


async def root():
    return success_payload()


async def validate():
    return success_payload()


async def validate_events():
    return success_payload({"payload": {"source": "sms-gate-events"}})


async def receive_sms(request: Request):
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)
    if not isinstance(parsed, dict):
        logging.warning("INCOMING /webhook/sms invalid body: %s", raw)
        return JSONResponse(status_code=400, content={"payload": {"success": False, "error": "invalid body"}})

    # SMS Gateway app webhook envelope: {"event":"sms:received","payload":{...}, ...}
    if _is_sms_gate_event(parsed):
        signature_error = _verify_sms_gate_signature(raw, request)
        if signature_error:
            logging.warning("Rejected webhook by signature validation: %s", signature_error)
            return JSONResponse(status_code=401, content={"payload": {"success": False, "error": signature_error}})

        delivery_id = parsed.get("id")
        if not _remember_delivery(delivery_id):
            logging.debug("Duplicate webhook delivery ignored (id=%s)", delivery_id)
            return JSONResponse(status_code=200, content=success_payload({"payload": {"duplicate": True}}))

        event_name = parsed.get("event")
        payload = parsed.get("payload") or {}

        if event_name in ("sms:received", "sms:data-received"):
            phone = payload.get("phoneNumber")
            message = payload.get("message")
            incoming_message_id = payload.get("messageId")
            has_pending_for_phone = any(phones_equivalent(key, phone) for key in list(pending_commands.keys()))

            if has_pending_for_phone:
                logging.info("INCOMING SMS GATE EVENT event=%s id=%s payload=%s", event_name, delivery_id, payload)
            else:
                logging.debug(
                    "INCOMING SMS GATE EVENT (idle) event=%s id=%s phone=%s messageId=%s",
                    event_name,
                    delivery_id,
                    phone,
                    incoming_message_id,
                )

            if not _remember_incoming_message(incoming_message_id):
                if has_pending_for_phone:
                    logging.info("Duplicate incoming SMS ignored by messageId=%s", incoming_message_id)
                else:
                    logging.debug("Duplicate incoming SMS ignored by messageId=%s", incoming_message_id)
                return JSONResponse(status_code=200, content=success_payload({"payload": {"duplicate": True}}))
            if event_name == "sms:data-received":
                # Keep base64 content as-is; parser/matcher can choose how to handle it.
                message = payload.get("data") or message

            normalized = {
                "from": phone,
                "sender": phone,
                "phone": phone,
                "message": message or "",
                "text": message or "",
                "body": message or "",
                "messageId": payload.get("messageId"),
                "simNumber": payload.get("simNumber"),
                "receivedAt": payload.get("receivedAt"),
                "event": event_name,
                "deviceId": parsed.get("deviceId"),
                "webhookId": parsed.get("webhookId"),
                "deliveryId": delivery_id,
                "raw_event": parsed
            }
            await _store_and_match_incoming(phone=phone, message=message, parsed=normalized)
            return JSONResponse(status_code=200, content=success_payload({"payload": {"event": event_name}}))

        if event_name in ("sms:sent", "sms:delivered", "sms:failed"):
            status_message_id = payload.get("messageId") or parsed.get("id")
            status_phone = payload.get("phoneNumber")
            quiet_status = _is_quiet_message_id(status_message_id)

            if not _remember_status_event(event_name, status_message_id, status_phone):
                logging.debug(
                    "Duplicate status event ignored%s event=%s messageId=%s phone=%s",
                    " (quiet)" if quiet_status else "",
                    event_name,
                    status_message_id,
                    status_phone,
                )
                return JSONResponse(status_code=200, content=success_payload({"payload": {"duplicate": True}}))

            if quiet_status:
                logging.debug("INCOMING SMS GATE EVENT (quiet) event=%s id=%s payload=%s", event_name, delivery_id, payload)
            else:
                logging.info("INCOMING SMS GATE EVENT event=%s id=%s payload=%s", event_name, delivery_id, payload)

            _update_status_from_sms_gate_event(event_name, payload, parsed, quiet=quiet_status)
            return JSONResponse(status_code=200, content=success_payload({"payload": {"event": event_name}}))

        if event_name in ("mms:received", "system:ping"):
            logging.info("Received event %s (ack only)", event_name)
            return JSONResponse(status_code=200, content=success_payload({"payload": {"event": event_name}}))

        logging.warning("Unknown webhook event ignored: %s", event_name)
        return JSONResponse(status_code=200, content=success_payload({"payload": {"event": event_name, "ignored": True}}))

    # Legacy format compatibility
    logging.info("INCOMING /webhook/sms BODY (legacy): %s", parsed)
    phone = parsed.get("from") or parsed.get("sender") or parsed.get("phone")
    message = parsed.get("message") or parsed.get("text") or parsed.get("body")
    await _store_and_match_incoming(phone=phone, message=message, parsed=parsed)
    return JSONResponse(status_code=200, content=success_payload({"payload": {"legacy": True}}))


def register_routes(app: FastAPI) -> None:
    app.get("/")(root)
    for path in ("/webhook/sms", "/webhook/sms/", "/webhook/sms-received", "/webhook/sms-received/"):
        app.get(path)(validate)
    for path in ("/webhook/sms/events", "/webhook/sms/events/"):
        app.get(path)(validate_events)
    for path in (
        "/webhook/sms",
        "/webhook/sms/",
        "/webhook/sms/events",
        "/webhook/sms/events/",
        "/webhook/sms-received",
        "/webhook/sms-received/",
    ):
        app.post(path)(receive_sms)