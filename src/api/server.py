# src/api/server.py  (actualizado)
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import json
from urllib.parse import parse_qs
import asyncio
import logging
import time
import uuid
import re
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone

# evento para que main espere el primer request
first_request_event: asyncio.Event = asyncio.Event()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI()

incoming_sms_queue: asyncio.Queue = asyncio.Queue()
outgoing_messages: asyncio.Queue = asyncio.Queue()
registered_devices: Dict[str, Dict[str, Any]] = {}
pending_commands: Dict[str, List[Dict[str, Any]]] = {}
message_statuses: Dict[str, Dict[str, Any]] = {}

# try to import dateutil parser for robust ISO parsing; fallback later
try:
    from dateutil import parser as dt_parser  # type: ignore
    _HAS_DATEUTIL = True
except Exception:
    _HAS_DATEUTIL = False

# ---------- Helpers ----------
def parse_body_bytes(raw: bytes, content_type: str) -> Dict[str, Any]:
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

def normalize_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    s = "".join(ch for ch in phone if ch.isdigit())
    return s

def _parse_iso_to_epoch(iso_str: str) -> Optional[int]:
    """Parsea una cadena ISO con zona a epoch (segundos). Devuelve None si falla."""
    if not iso_str:
        return None
    try:
        if _HAS_DATEUTIL:
            dt = dt_parser.isoparse(iso_str)
            # dt may be timezone-aware
            ts = int(dt.timestamp())
            return ts
        else:
            # try fromisoformat with fallback for trailing Z
            s = iso_str
            if s.endswith("Z"):
                s = s.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp())
            except Exception:
                return None
    except Exception:
        return None

# pending/command helpers
async def send_command_and_wait(to: str, text: str, match_fn: Optional[Callable[[str], bool]] = None, timeout: int = 30) -> Dict[str, Any]:
    if not to or not text:
        raise ValueError("to and text required")
    cmd_id = str(uuid.uuid4())[:8]
    payload = {"id": cmd_id, "to": to, "message": text, "meta": {"cmd_id": cmd_id, "timestamp": int(time.time())}}
    await outgoing_messages.put(payload)
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    key = normalize_phone(to)
    entry = {"id": cmd_id, "future": fut, "match_fn": match_fn, "created_at": int(time.time()), "to": key}
    pending_commands.setdefault(key, []).append(entry)
    logging.info("Enqueued command %s for %s", cmd_id, key)
    try:
        result = await asyncio.wait_for(fut, timeout=timeout)
        return result
    finally:
        lst = pending_commands.get(key, [])
        pending_commands[key] = [e for e in lst if e["id"] != cmd_id]
        if not pending_commands.get(key):
            pending_commands.pop(key, None)

async def _handle_incoming_and_try_match(parsed: Dict[str, Any]):
    phone = parsed.get("from") or parsed.get("sender") or parsed.get("phone")
    norm = normalize_phone(phone)
    message_text = parsed.get("message") or parsed.get("text") or parsed.get("body") or ""
    logging.info("Handling inbound for matching: from=%s msg=%s", norm, message_text[:120])
    if not norm:
        return
    entries = pending_commands.get(norm, [])
    for e in list(entries):
        match_fn = e.get("match_fn")
        try:
            matched = False
            if match_fn:
                try:
                    matched = bool(match_fn(message_text))
                except Exception as ex:
                    logging.warning("match_fn error: %s", ex)
                    matched = False
            else:
                matched = True
            if matched and not e["future"].done():
                e["future"].set_result({"from": phone, "message": message_text, "raw": parsed})
                logging.info("Resolved pending command %s for %s", e["id"], norm)
                break
        except Exception as ex:
            logging.exception("Error while matching pending command: %s", ex)

# ---------- Endpoints ----------

@app.get("/")
async def root():
    return success_payload()

@app.get("/webhook/sms")
@app.get("/webhook/sms/")
async def validate():
    return success_payload()

@app.post("/webhook/sms")
@app.post("/webhook/sms/")
async def receive_sms(request: Request):
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)
    logging.info("INCOMING /webhook/sms BODY: %s", parsed)
    phone = parsed.get("from") or parsed.get("sender") or parsed.get("phone")
    message = parsed.get("message") or parsed.get("text") or parsed.get("body")
    # enqueue incoming
    await incoming_sms_queue.put({"phone": phone, "message": message, "raw": parsed})
    # attempt to store in DB if app_state available
    try:
        import importlib
        app_state = None
        try:
            mod = importlib.import_module("src.core.app_state")
            app_state = getattr(mod, "app_state", None)
        except Exception:
            try:
                mod = importlib.import_module("core.app_state")
                app_state = getattr(mod, "app_state", None)
            except Exception:
                app_state = None
        if app_state and getattr(app_state, "insert_incoming", None):
            try:
                app_state.insert_incoming(phone, message, parsed)
            except Exception as ex:
                logging.debug("Error saving incoming SMS to DB: %s", ex)
    except Exception:
        pass

    try:
        await _handle_incoming_and_try_match(parsed)
    except Exception as ex:
        logging.exception("Error matching incoming SMS: %s", ex)
    return JSONResponse(status_code=200, content=success_payload())

@app.api_route("/webhook/sms/device", methods=["POST", "PATCH", "PUT"])
@app.api_route("/webhook/sms/device/", methods=["POST", "PATCH", "PUT"])
async def register_device(request: Request):
    try:
        if not first_request_event.is_set():
            first_request_event.set()
            logging.info("first_request_event set by /webhook/sms/device")
    except Exception:
        logging.exception("Error setting first_request_event")
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)
    if not parsed:
        qp = dict(request.query_params)
        if qp:
            parsed = qp
    logging.info("DEVICE REGISTRATION (%s) BODY: %s", request.method, parsed)
    device_name = parsed.get("name") or parsed.get("device") or parsed.get("deviceName")
    push_token = parsed.get("pushToken") or parsed.get("token")
    login = parsed.get("login") or device_name or (push_token.split(":")[0] if push_token else None)
    if not login:
        login = f"device_{int(time.time())}"
    existing = registered_devices.get(login)
    registered_devices[login] = {
        "name": device_name or (existing.get("name") if existing else None),
        "pushToken": push_token or (existing.get("pushToken") if existing else None),
        "raw": parsed,
        "last_seen": int(time.time()),
        "method": request.method
    }
    # try persist device if app_state present
    try:
        import importlib
        app_state = None
        try:
            mod = importlib.import_module("src.core.app_state")
            app_state = getattr(mod, "app_state", None)
        except Exception:
            try:
                mod = importlib.import_module("core.app_state")
                app_state = getattr(mod, "app_state", None)
            except Exception:
                app_state = None
        if app_state and getattr(app_state, "save_device", None):
            try:
                app_state.save_device(login, registered_devices[login]["name"], registered_devices[login]["pushToken"], parsed)
            except Exception as ex:
                logging.debug("Error saving device to DB: %s", ex)
    except Exception:
        pass

    logging.info("REGISTERED/UPDATED DEVICE '%s' -> %s", login, registered_devices[login])
    response = {
        "login": login,
        "deviceId": login,
        "deviceName": registered_devices[login]["name"],
        "payload": {"success": True, "error": None, "login": login}
    }
    return JSONResponse(status_code=200, content=response)

# ---------- CRITICAL: GET /message (returns root array) ----------
@app.get("/webhook/sms/message")
@app.get("/webhook/sms/message/")
async def get_messages():
    items: List[Dict[str, Any]] = []
    try:
        if not first_request_event.is_set():
            first_request_event.set()
            logging.info("first_request_event set by GET /webhook/sms/message")
    except Exception:
        logging.exception("Error setting first_request_event")
    try:
        while True:
            msg = outgoing_messages.get_nowait()
            if not isinstance(msg, dict):
                msg = {"to": "", "message": str(msg)}
            msg_id = msg.get("id") or str(uuid.uuid4())
            # support phoneNumbers or convert to list
            phone_numbers = []
            if isinstance(msg.get("phoneNumbers"), list) and msg.get("phoneNumbers"):
                phone_numbers = msg["phoneNumbers"]
            else:
                to_raw = msg.get("to") or msg.get("phone") or msg.get("number") or ""
                if isinstance(to_raw, list):
                    phone_numbers = to_raw
                elif isinstance(to_raw, str) and to_raw.strip():
                    phone_numbers = [to_raw]
            if not phone_numbers:
                phone_numbers = [""]
            text = msg.get("message") or msg.get("body") or ""
            meta = msg.get("meta") or {"generated_at": int(time.time())}
            item = {
                "id": msg_id,
                "to": phone_numbers[0] if phone_numbers and phone_numbers[0] else "",
                "phoneNumbers": phone_numbers,
                "message": text,
                "body": text,
                "meta": meta
            }
            items.append(item)
    except asyncio.QueueEmpty:
        pass
    return JSONResponse(status_code=200, content=items, headers={"Content-Type": "application/json; charset=utf-8"})

# POST /message -> enqueue outgoing messages (accepts to or phoneNumbers)
@app.post("/webhook/sms/message")
@app.post("/webhook/sms/message/")
async def post_message(request: Request):
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)
    logging.info("POST /message BODY: %s", parsed)
    phone_numbers = []
    if isinstance(parsed.get("phoneNumbers"), list) and parsed.get("phoneNumbers"):
        phone_numbers = parsed["phoneNumbers"]
    else:
        to_raw = parsed.get("to") or parsed.get("phone") or parsed.get("number") or ""
        if isinstance(to_raw, list):
            phone_numbers = to_raw
        elif isinstance(to_raw, str) and to_raw.strip():
            phone_numbers = [to_raw]
    text = parsed.get("message") or parsed.get("text") or parsed.get("body")
    msg_id = parsed.get("id") or str(uuid.uuid4())
    if phone_numbers and text:
        await outgoing_messages.put({
            "id": msg_id,
            "to": phone_numbers[0],
            "phoneNumbers": phone_numbers,
            "message": text,
            "meta": parsed.get("meta") or {"from_server": True, "ts": int(time.time())}
        })
        # try persist command if app_state available
        try:
            import importlib
            app_state = None
            try:
                mod = importlib.import_module("src.core.app_state")
                app_state = getattr(mod, "app_state", None)
            except Exception:
                try:
                    mod = importlib.import_module("core.app_state")
                    app_state = getattr(mod, "app_state", None)
                except Exception:
                    app_state = None
            if app_state and getattr(app_state, "save_command", None):
                try:
                    app_state.save_command(msg_id, phone_numbers[0], phone_numbers, text, parsed.get("meta") or {}, state="Queued")
                except Exception as ex:
                    logging.debug("Error saving command to DB: %s", ex)
        except Exception:
            pass
        return JSONResponse(status_code=200, content=success_payload())
    return JSONResponse(status_code=400, content={"payload": {"success": False, "error": "missing phoneNumbers/to or message"}})

# webhooks -> return root array
@app.get("/webhook/sms/webhooks")
@app.get("/webhook/sms/webhooks/")
async def get_webhooks():
    webhooks_list: List[Dict[str, Any]] = []
    return JSONResponse(status_code=200, content=webhooks_list, headers={"Content-Type": "application/json; charset=utf-8"})

@app.get("/webhook/sms/settings")
@app.get("/webhook/sms/settings/")
async def get_settings():
    return JSONResponse(status_code=200, content={"settings": {"push": True, "pollInterval": 30}})

# Debug/admin
@app.get("/_debug/registered_devices")
async def debug_registered_devices():
    return JSONResponse(status_code=200, content={"registered": registered_devices})

@app.get("/_debug/peek_outgoing")
async def peek_outgoing():
    items: List[Dict[str, Any]] = []
    try:
        while True:
            it = outgoing_messages.get_nowait()
            items.append(it)
    except asyncio.QueueEmpty:
        pass
    for it in items:
        await outgoing_messages.put(it)
    return JSONResponse(status_code=200, content={"outgoing": items})

# ----------------------------
# NEW: PATCH handler with enhanced logging & DB updates
# ----------------------------
@app.api_route("/webhook/sms/message", methods=["PATCH", "PUT"])
@app.api_route("/webhook/sms/message/", methods=["PATCH", "PUT"])
async def patch_messages(request: Request):
    """
    Procesa reportes de estado (array raíz o único objeto).
    Logea motivo y actualiza DB/status in-memory according to policy:
      - ERROR: envío fallido (Failed) -> incluir la razón (recipient.error)
      - WARNING: enviado/entregado pero sin respuesta -> avisar
      - SUCCESS: entregado y ya existe respuesta entrante correlacionada
      - INFO: otros estados/confirmaciones
    """
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)

    items = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict) and parsed:
        items = [parsed]
    else:
        logging.warning("PATCH /webhook/sms/message received empty or invalid body: %s", raw)
        return JSONResponse(status_code=400, content={"payload": {"success": False, "error": "invalid body"}})

    now_ts = int(time.time())

    # try importing app_state for DB operations if available
    app_state = None
    try:
        import importlib
        try:
            mod = importlib.import_module("src.core.app_state")
            app_state = getattr(mod, "app_state", None)
        except Exception:
            try:
                mod = importlib.import_module("core.app_state")
                app_state = getattr(mod, "app_state", None)
            except Exception:
                app_state = None
    except Exception:
        app_state = None

    for it in items:
        msg_id = it.get("id") or it.get("messageId") or str(uuid.uuid4())
        state = it.get("state") or None
        recipients = it.get("recipients") or []
        states_history = it.get("states") or {}

        # normalize recipients
        normalized_recipients = []
        if isinstance(recipients, list):
            for r in recipients:
                if isinstance(r, dict):
                    normalized_recipients.append({
                        "phoneNumber": r.get("phoneNumber") or r.get("phone") or "",
                        "state": r.get("state") or None,
                        "error": r.get("error") or None
                    })
                else:
                    normalized_recipients.append({"phoneNumber": str(r), "state": None, "error": None})

        # save status in-memory and optionally to DB
        message_statuses[msg_id] = {
            "id": msg_id,
            "state": state,
            "recipients": normalized_recipients,
            "states": states_history,
            "updated_at": now_ts,
            "raw": it,
            "last_reason": None
        }
        # persist status if app_state available
        if app_state and getattr(app_state, "save_status", None):
            try:
                app_state.save_status(msg_id, message_statuses[msg_id])
            except Exception as ex:
                logging.debug("Error saving status to DB: %s", ex)

        logging.info("Message status updated: %s -> %s", msg_id, message_statuses[msg_id])

        # Evaluate and log per recipient
        for r in normalized_recipients:
            phone = r.get("phoneNumber") or ""
            rstate = (r.get("state") or "").lower()
            rerr = r.get("error")
            message_statuses[msg_id]['last_reason'] = rerr or message_statuses[msg_id].get('last_reason')

            # Try to detect if there is already an incoming response from that phone
            has_response = False
            try:
                if app_state and getattr(app_state, "conn", None):
                    ref_ts = now_ts
                    proc_ts = None
                    proc_str = None
                    if isinstance(states_history, dict):
                        for k, v in states_history.items():
                            if k and isinstance(k, str) and k.lower() in ("processed", "sent", "delivered"):
                                proc_str = v
                                break
                    if proc_str:
                        parsed_ts = _parse_iso_to_epoch(proc_str)
                        if parsed_ts:
                            proc_ts = parsed_ts
                    if proc_ts:
                        ref_ts = proc_ts
                    # check messages_in for a recent reply from this phone after ref_ts-10s
                    try:
                        c = app_state.conn.cursor()
                        lower_bound = int(ref_ts) - 10
                        c.execute("SELECT message, received_at FROM messages_in WHERE from_phone=? AND received_at>=? ORDER BY received_at DESC LIMIT 1", (phone, lower_bound))
                        row = c.fetchone()
                        if row:
                            has_response = True
                    except Exception as ex:
                        logging.debug("DB query error checking response: %s", ex)
            except Exception:
                has_response = False

            # Decide log level and message
            if rstate == "failed":
                reason = rerr or "Send failed (unknown reason)"
                message_statuses[msg_id]['last_reason'] = reason
                # update DB command state if possible
                if app_state and getattr(app_state, "update_command_state", None):
                    try:
                        app_state.update_command_state(msg_id, "Failed")
                    except Exception as ex:
                        logging.debug("Error updating command state in DB: %s", ex)
                logging.error("%s error final: %s (msg_id=%s)", phone, reason, msg_id)
            elif rstate in ("processed", "sent"):
                # processed by phone (likely sent)
                if has_response:
                    if app_state and getattr(app_state, "update_command_state", None):
                        try:
                            app_state.update_command_state(msg_id, "Replied")
                        except Exception:
                            pass
                    logging.info("%s enviado y respondido (msg_id=%s)", phone, msg_id)
                else:
                    # not yet replied
                    logging.warning("%s enviado pero sin respuesta aún (msg_id=%s)", phone, msg_id)
            elif rstate == "delivered":
                if has_response:
                    if app_state and getattr(app_state, "update_command_state", None):
                        try:
                            app_state.update_command_state(msg_id, "Replied")
                        except Exception:
                            pass
                    logging.info("%s entregado y respondido (msg_id=%s)", phone, msg_id)
                else:
                    logging.warning("%s entregado pero sin respuesta (msg_id=%s)", phone, msg_id)
            else:
                # unexpected or other states => INFO
                if has_response:
                    logging.info("%s estado=%s, pero ya hay respuesta (msg_id=%s)", phone, rstate or "unknown", msg_id)
                else:
                    logging.info("%s estado=%s (msg_id=%s)", phone, rstate or "unknown", msg_id)

    return JSONResponse(status_code=200, content={"payload": {"success": True, "error": None}})

# Admin/send_command remains as before
@app.post("/_admin/send_command")
async def admin_send_command(request: Request):
    body = await request.json()
    to = body.get("to")
    message = body.get("message")
    pattern = body.get("pattern")
    if not to or not message:
        raise HTTPException(status_code=400, detail="to and message required")
    match_fn = None
    if pattern:
        regex = re.compile(pattern)
        match_fn = lambda m: bool(regex.search(m or ""))
    try:
        res = await send_command_and_wait(to=to, text=message, match_fn=match_fn, timeout=int(body.get("timeout", 30)))
        return JSONResponse(status_code=200, content={"status": "ok", "response": res})
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="timeout waiting for response")