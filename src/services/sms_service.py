import asyncio
import time
import uuid

import httpx
from loguru import logger

from ..api.state import register_quiet_message_id, send_command_via_local_api_and_wait
from ..core.config import settings
from ..core.parser import parse_response
from ..core.phones import format_phone_for_local_api


class SMSService:

    def __init__(self, retries: int = 3, delay: int = 5, timeout: int = 10):
        self.retries = retries
        self.delay = delay
        self.timeout = timeout
        self.local_api_base_url = settings.local_api_base_url
        self.local_api_username = settings.local_api_username
        self.local_api_password = settings.local_api_password
        self.local_api_enabled = settings.local_api_enabled
        if self.local_api_enabled:
            logger.info("SMSService en modo local API directo (ADB/local server), sin polling cloud/private.")

    async def send_with_retry(self, phone: str, message: str, expected: str) -> dict:
        attempt = 0

        while attempt < self.retries:
            try:
                logger.debug(f"Enviando intento {attempt + 1}/{self.retries} a {phone}")

                response = await send_command_via_local_api_and_wait(
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
        try:
            register_quiet_message_id(message_id)
        except Exception:
            pass
        api_phone = format_phone_for_local_api(phone)
        payload = {
            "id": message_id,
            "to": api_phone,
            "phoneNumbers": [api_phone],
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

        raise RuntimeError("SMS_GATE_LOCAL_API_ENABLED is false; polling send path was removed")
