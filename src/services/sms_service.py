import asyncio
import os
import time
import uuid

import httpx
from loguru import logger

from ..api import server as server_module
from ..api.server import send_command_and_wait, send_command_via_local_api_and_wait
from ..core.parser import parse_response


class SMSService:

    def __init__(self, retries: int = 3, delay: int = 5, timeout: int = 10):
        self.retries = retries
        self.delay = delay
        self.timeout = timeout
        self.local_api_base_url = os.getenv("SMS_GATE_LOCAL_API_BASE_URL", "http://127.0.0.1:18080").strip().rstrip("/")
        self.local_api_username = os.getenv("SMS_GATE_LOCAL_API_USERNAME", "sms").strip()
        self.local_api_password = os.getenv("SMS_GATE_LOCAL_API_PASSWORD", "")
        raw = os.getenv("SMS_GATE_LOCAL_API_ENABLED", "0").strip().lower()
        self.local_api_enabled = raw in {"1", "true", "yes", "on"}
        if self.local_api_enabled:
            logger.info("SMSService en modo local API directo (ADB/local server), sin polling cloud/private.")

    async def send_with_retry(self, phone: str, message: str, expected: str) -> dict:
        attempt = 0

        while attempt < self.retries:
            try:
                logger.debug(f"Enviando intento {attempt + 1}/{self.retries} a {phone}")

                if self.local_api_enabled:
                    response = await send_command_via_local_api_and_wait(
                        to=phone,
                        text=message,
                        match_fn=None,  # Resolver con cualquier respuesta, luego evaluamos expected.
                        timeout=self.timeout
                    )
                else:
                    response = await send_command_and_wait(
                        to=phone,
                        text=message,
                        match_fn=None,  # Resolver con cualquier respuesta, luego evaluamos expected.
                        timeout=self.timeout
                    )

                raw_message = response.get("message", "")
                expected_ok = parse_response(raw_message, expected)
                if expected_ok:
                    return {
                        "status": "ONLINE",
                        "error_code": "",
                        "raw_message": raw_message,
                    }

                return {
                    "status": "UNKNOWN",
                    "error_code": "",
                    "raw_message": raw_message,
                }

            except asyncio.TimeoutError:
                attempt += 1
                logger.warning(f"Timeout en intento {attempt}/{self.retries} para {phone}")

            except Exception as e:
                logger.error(f"Error inesperado con {phone}: {e}")
                raise

            if attempt < self.retries:
                await asyncio.sleep(self.delay)

        return {
            "status": "OFFLINE",
            "error_code": "NO_RESPONSE_TIMEOUT",
            "raw_message": "",
        }

    async def send_notification(self, phone: str, message: str) -> dict:
        if not phone or not message:
            raise ValueError("phone and message required")

        message_id = str(uuid.uuid4())[:8]
        payload = {
            "id": message_id,
            "to": phone,
            "phoneNumbers": [phone],
            "message": message,
            "meta": {"notification": True, "timestamp": int(time.time())},
        }

        if self.local_api_enabled:
            url = f"{self.local_api_base_url}/message"
            auth = httpx.BasicAuth(username=self.local_api_username, password=self.local_api_password)
            last_error = None
            for attempt in range(1, 4):
                try:
                    async with httpx.AsyncClient(timeout=15.0, trust_env=False) as client:
                        res = await client.post(url, auth=auth, json=payload)
                    if res.status_code >= 400:
                        raise RuntimeError(
                            f"Local API notification failed status={res.status_code} body={res.text[:500]}"
                        )
                    last_error = None
                    break
                except Exception as ex:
                    last_error = ex
                    logger.warning(f"Local API notification attempt {attempt}/3 failed for {phone}: {ex}")
                    if attempt < 3:
                        await asyncio.sleep(0.5)
            if last_error is not None:
                raise last_error
            return {"status": "SENT", "message_id": message_id}

        await server_module.outgoing_messages.put(payload)
        return {"status": "QUEUED", "message_id": message_id}
