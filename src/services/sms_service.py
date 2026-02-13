import asyncio
from loguru import logger
from .simulator import send_sms, TimeoutException
from ..core.parser import parse_response


class SMSService:

    def __init__(self, retries: int = 3, delay: int = 5, timeout: int = 10):
        self.retries = retries
        self.delay = delay
        self.timeout = timeout

    async def send_with_retry(self, phone: str, message: str, expected: str) -> str:
        attempt = 0

        while attempt < self.retries:
            try:
                logger.debug(f"Enviando intento {attempt + 1}/{self.retries} a {phone}")
                # Timeout controlado
                response = await asyncio.wait_for(
                    send_sms(phone, message),
                    timeout=self.timeout
                )

                status = parse_response(response, expected)
                return status

            except TimeoutException:
                attempt += 1
                logger.warning(f"Timeout en intento {attempt}/{self.retries} para {phone}")
                print("\n")

            except asyncio.TimeoutError:
                attempt += 1
                logger.warning(f"Timeout asyncio en intento {attempt}/{self.retries} para {phone}")
                print("\n")
                
            except Exception as e:
                logger.error(f"Error inesperado con {phone}: {e}")
                raise

            if attempt < self.retries:
                await asyncio.sleep(self.delay)

        # Si agotó reintentos
        raise TimeoutException(f"Dispositivo {phone} no respondió tras {self.retries} intentos")
