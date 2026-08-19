import asyncio
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set

import httpx
from loguru import logger

from ..core.config import settings
from ..core.phones import format_phone_for_local_api, normalize_phone, phones_equivalent

logger = logger.bind(component="state")

incoming_sms_queue: asyncio.Queue = asyncio.Queue()
pending_commands: Dict[str, List[Dict[str, Any]]] = {}
message_statuses: Dict[str, Dict[str, Any]] = {}
recent_delivery_ids_order: deque = deque()
recent_delivery_ids_set: Set[str] = set()
recent_incoming_message_ids_order: deque = deque()
recent_incoming_message_ids_set: Set[str] = set()
recent_status_event_keys_order: deque = deque()
recent_status_event_keys_set: Set[str] = set()
quiet_outbound_message_ids_order: deque = deque()
quiet_outbound_message_ids_set: Set[str] = set()


def _get_local_api_runtime_config() -> Dict[str, Any]:
    return {
        "enabled": settings.local_api_enabled,
        "base_url": settings.local_api_base_url,
        "username": settings.local_api_username,
        "password": settings.local_api_password,
    }


def _remember_delivery(delivery_id: Optional[str]) -> bool:
    if not delivery_id:
        return True

    if delivery_id in recent_delivery_ids_set:
        return False

    if len(recent_delivery_ids_order) >= settings.max_tracked_deliveries:
        oldest = recent_delivery_ids_order.popleft()
        recent_delivery_ids_set.discard(oldest)

    recent_delivery_ids_order.append(delivery_id)
    recent_delivery_ids_set.add(delivery_id)
    return True


def _remember_incoming_message(message_id: Optional[str]) -> bool:
    if not message_id:
        return True

    if message_id in recent_incoming_message_ids_set:
        return False

    if len(recent_incoming_message_ids_order) >= settings.max_tracked_deliveries:
        oldest = recent_incoming_message_ids_order.popleft()
        recent_incoming_message_ids_set.discard(oldest)

    recent_incoming_message_ids_order.append(message_id)
    recent_incoming_message_ids_set.add(message_id)
    return True


def _status_event_key(event_name: Optional[str], message_id: Optional[str], phone: Optional[str]) -> str:
    return f"{event_name or ''}|{message_id or ''}|{normalize_phone(phone)}"


def _remember_status_event(event_name: Optional[str], message_id: Optional[str], phone: Optional[str]) -> bool:
    key = _status_event_key(event_name, message_id, phone)
    if not key.strip("|"):
        return True

    if key in recent_status_event_keys_set:
        return False

    if len(recent_status_event_keys_order) >= settings.max_tracked_deliveries:
        oldest = recent_status_event_keys_order.popleft()
        recent_status_event_keys_set.discard(oldest)

    recent_status_event_keys_order.append(key)
    recent_status_event_keys_set.add(key)
    return True


def register_quiet_message_id(message_id: Optional[str]):
    if not message_id:
        return
    if message_id in quiet_outbound_message_ids_set:
        return
    if len(quiet_outbound_message_ids_order) >= settings.max_tracked_deliveries:
        oldest = quiet_outbound_message_ids_order.popleft()
        quiet_outbound_message_ids_set.discard(oldest)
    quiet_outbound_message_ids_order.append(message_id)
    quiet_outbound_message_ids_set.add(message_id)


def _is_quiet_message_id(message_id: Optional[str]) -> bool:
    if not message_id:
        return False
    return message_id in quiet_outbound_message_ids_set


async def send_command_via_local_api_and_wait(
    to: str,
    text: str,
    match_fn: Optional[Callable[[str], bool]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    if not to or not text:
        raise ValueError("to and text required")
    runtime_cfg = _get_local_api_runtime_config()
    if not runtime_cfg["enabled"]:
        raise RuntimeError("SMS_GATE_LOCAL_API_ENABLED is false")

    cmd_id = str(uuid.uuid4())[:8]
    loop = asyncio.get_event_loop()
    fut = loop.create_future()
    key = normalize_phone(to)
    entry = {"id": cmd_id, "future": fut, "match_fn": match_fn, "created_at": int(time.time()), "to": key}
    pending_commands.setdefault(key, []).append(entry)
    logger.info("Registered pending local-api command {} for {}", cmd_id, key)

    try:
        body = {
            "id": cmd_id,
            "message": text,
            "phoneNumbers": [format_phone_for_local_api(to)],
        }
        url = f"{runtime_cfg['base_url']}/message"
        auth = httpx.BasicAuth(username=runtime_cfg["username"], password=runtime_cfg["password"])
        send_error = None
        for send_attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                    res = await client.post(url, auth=auth, json=body)
                if res.status_code >= 400:
                    raise RuntimeError(f"Local API send failed status={res.status_code} body={res.text[:500]}")
                send_error = None
                break
            except Exception as ex:
                send_error = ex
                logger.warning("Local API send attempt {} failed for {}: {}", send_attempt, to, ex)
                if send_attempt < 3:
                    await asyncio.sleep(0.5)
        if send_error is not None:
            raise send_error
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
    if not norm:
        logger.debug("Inbound without normalized phone; skipping matcher.")
        return
    candidate_keys = []
    for key in list(pending_commands.keys()):
        if phones_equivalent(key, norm):
            candidate_keys.append(key)

    if candidate_keys:
        logger.info("Handling inbound for matching: from={} msg={}", norm, message_text[:120])
    else:
        logger.debug("Inbound received while idle (no pending match): from={}", norm)

    for key in candidate_keys:
        entries = pending_commands.get(key, [])
        for e in list(entries):
            match_fn = e.get("match_fn")
            try:
                matched = False
                if match_fn:
                    try:
                        matched = bool(match_fn(message_text))
                    except Exception as ex:
                        logger.warning("match_fn error: {}", ex)
                        matched = False
                else:
                    matched = True
                if matched and not e["future"].done():
                    e["future"].set_result({"from": phone, "message": message_text, "raw": parsed})
                    logger.info("Resolved pending command {} for inbound={} pending_key={}", e["id"], norm, key)
                    return
            except Exception as ex:
                logger.exception("Error while matching pending command: {}", ex)