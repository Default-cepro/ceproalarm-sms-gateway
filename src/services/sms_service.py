import asyncio
import os
from loguru import logger
from ..api.server import send_command_and_wait, send_command_via_local_api_and_wait
from ..core.parser import parse_response


class SMSService:

    def __init__(self, retries: int = 3, delay: int = 5, timeout: int = 10):
        self.retries = retries
        self.delay = delay
        self.timeout = timeout
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
                        "status": "operativo",
                        "error_code": "",
                        "raw_message": raw_message,
                    }

                return {
                    "status": "operativo sin respuesta esperada",
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
            "status": "inoperativo",
            "error_code": "NO_RESPONSE_TIMEOUT",
            "raw_message": "",
        }
