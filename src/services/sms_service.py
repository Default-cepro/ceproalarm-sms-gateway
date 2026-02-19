import asyncio
from loguru import logger
from ..api.server import send_command_and_wait
from ..core.parser import parse_response


class SMSService:

    def __init__(self, retries: int = 3, delay: int = 5, timeout: int = 10):
        self.retries = retries
        self.delay = delay
        self.timeout = timeout

    async def send_with_retry(self, phone: str, message: str, expected: str) -> str:
        attempt = 0

        # función de match basada en el expected del JSON
        def match_fn(response_text: str) -> bool:
            return expected in (response_text or "")

        while attempt < self.retries:
            try:
                logger.debug(f"Enviando intento {attempt + 1}/{self.retries} a {phone}")

                response = await send_command_and_wait(
                    to=phone,
                    text=message,
                    match_fn=match_fn,
                    timeout=self.timeout
                )

                raw_message = response.get("message", "")
                status = parse_response(raw_message, expected)
                return status

            except asyncio.TimeoutError:
                attempt += 1
                logger.warning(f"Timeout en intento {attempt}/{self.retries} para {phone}")

            except Exception as e:
                logger.error(f"Error inesperado con {phone}: {e}")
                raise

            if attempt < self.retries:
                await asyncio.sleep(self.delay)

        raise Exception(f"Dispositivo {phone} no respondió tras {self.retries} intentos")
