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

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI()

incoming_sms_queue: asyncio.Queue = asyncio.Queue()
outgoing_messages: asyncio.Queue = asyncio.Queue()
registered_devices: Dict[str, Dict[str, Any]] = {}
pending_commands: Dict[str, List[Dict[str, Any]]] = {}
message_statuses: Dict[str, Dict[str, Any]] = {}

# --- endpoint para recibir reportes de estado (PATCH /webhook/sms/message) ---
@app.api_route("/webhook/sms/message", methods=["PATCH", "PUT"])
@app.api_route("/webhook/sms/message/", methods=["PATCH", "PUT"])
async def patch_messages(request: Request):
    """
    Procesa el reporte de estados enviado por la app.
    Espera un ARRAY raíz con objetos similares al ejemplo:
    [
      {
        "id":"...", "recipients":[{"phoneNumber":"+...","state":"Failed","error":"..."}], "state":"Failed", "states": {...}
      }
    ]
    """
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)

    # Aceptar lista o un único objeto
    items = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict) and parsed:
        # a veces viene como objeto único en vez de array
        items = [parsed]
    else:
        logging.warning("PATCH /webhook/sms/message received empty or invalid body: %s", raw)
        return JSONResponse(status_code=400, content={"payload": {"success": False, "error": "invalid body"}})

    now_ts = int(time.time())
    for it in items:
        msg_id = it.get("id") or it.get("messageId") or str(uuid.uuid4())
        state = it.get("state") or None
        recipients = it.get("recipients") or []
        states_history = it.get("states") or {}

        # normalizar recipients list
        normalized_recipients = []
        if isinstance(recipients, list):
            for r in recipients:
                # r puede ser dict con phoneNumber,state,error
                if isinstance(r, dict):
                    normalized_recipients.append({
                        "phoneNumber": r.get("phoneNumber") or r.get("phone") or "",
                        "state": r.get("state") or None,
                        "error": r.get("error") or None
                    })
                else:
                    # si viene como string
                    normalized_recipients.append({"phoneNumber": str(r), "state": None, "error": None})
        # guardar estado en memoria (merge si ya existe)
        message_statuses[msg_id] = {
            "id": msg_id,
            "state": state,
            "recipients": normalized_recipients,
            "states": states_history,
            "updated_at": now_ts,
            "raw": it
        }
        logging.info("Message status updated: %s -> %s", msg_id, message_statuses[msg_id])

    # devolver 200 OK simple para que la app deje de reintentar
    return JSONResponse(status_code=200, content={"payload": {"success": True, "error": None}})

# --- debug endpoint para ver estados reportados ---
@app.get("/_debug/message_statuses")
async def debug_message_statuses():
    return JSONResponse(status_code=200, content={"statuses": message_statuses})


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

# pending/command helpers (kept from la versión anterior)
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
    await incoming_sms_queue.put({"phone": phone, "message": message, "raw": parsed})
    try:
        await _handle_incoming_and_try_match(parsed)
    except Exception as ex:
        logging.exception("Error matching incoming SMS: %s", ex)
    return JSONResponse(status_code=200, content=success_payload())

@app.api_route("/webhook/sms/device", methods=["POST", "PATCH", "PUT"])
@app.api_route("/webhook/sms/device/", methods=["POST", "PATCH", "PUT"])
async def register_device(request: Request):
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
    logging.info("REGISTERED/UPDATED DEVICE '%s' -> %s", login, registered_devices[login])
    response = {
        "login": login,
        "deviceId": login,
        "deviceName": registered_devices[login]["name"],
        "payload": {"success": True, "error": None, "login": login}
    }
    return JSONResponse(status_code=200, content=response)

# -------------- CRÍTICO: GET /message (DEVUELVE ARRAY RAÍZ) --------------
@app.get("/webhook/sms/message")
@app.get("/webhook/sms/message/")
async def get_messages():
    items: List[Dict[str, Any]] = []
    try:
        while True:
            msg = outgoing_messages.get_nowait()
            if not isinstance(msg, dict):
                msg = {"to": "", "message": str(msg)}
            msg_id = msg.get("id") or str(uuid.uuid4())
            # admitir phoneNumbers si ya viene como lista o convertir "to" a lista
            phone_numbers = []
            if isinstance(msg.get("phoneNumbers"), list) and msg.get("phoneNumbers"):
                phone_numbers = msg["phoneNumbers"]
            else:
                to_raw = msg.get("to") or msg.get("phone") or msg.get("number") or ""
                # si to_raw es una lista, convertirla; si es string, ponerla en lista
                if isinstance(to_raw, list):
                    phone_numbers = to_raw
                elif isinstance(to_raw, str) and to_raw.strip():
                    phone_numbers = [to_raw]
            # asegurar campo no nulo: si no hay números, poner lista vacía? la app exige no-nulo,
            # mejor devolver lista vacía que null, pero preferible que tenga al menos una cadena vacía
            if not phone_numbers:
                phone_numbers = [""]
            text = msg.get("message") or msg.get("body") or ""
            meta = msg.get("meta") or {"generated_at": int(time.time())}
            # construir la forma esperada: incluir phoneNumbers (lista), to (primero), body, message, id, meta
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

# POST /message -> encolar mensajes (acepta to o phoneNumbers)
@app.post("/webhook/sms/message")
@app.post("/webhook/sms/message/")
async def post_message(request: Request):
    raw = await request.body()
    ct = request.headers.get("content-type", "")
    parsed = parse_body_bytes(raw, ct)
    logging.info("POST /message BODY: %s", parsed)
    # Si el cliente envía phoneNumbers como lista, usarla; si envía 'to' como string, convertir
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
        # Encolar con phoneNumbers y to (primero)
        await outgoing_messages.put({
            "id": msg_id,
            "to": phone_numbers[0],
            "phoneNumbers": phone_numbers,
            "message": text,
            "meta": parsed.get("meta") or {"from_server": True, "ts": int(time.time())}
        })
        return JSONResponse(status_code=200, content=success_payload())
    return JSONResponse(status_code=400, content={"payload": {"success": False, "error": "missing phoneNumbers/to or message"}})

# webhooks -> DEVUELVE ARRAY RAÍZ
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
