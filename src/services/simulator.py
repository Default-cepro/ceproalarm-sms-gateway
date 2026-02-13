import random
import asyncio
from loguru import logger


class TimeoutException(Exception):
    pass


async def send_sms(phone: str, message: str) -> str:
    print(f"Enviando SMS a {phone}: {message}")
    logger.debug(f"Tipo de send_sms: {type(send_sms)}")
    # Simular latencia real sin bloquear el event loop
    await asyncio.sleep(random.uniform(0.5, 2.0))

    probability = random.random()

    # 20% timeout
    if probability < 0.2:
        raise TimeoutException("Timeout")

    # 10% error raro
    if probability < 0.3:
        return "ERROR"

    # 70% éxito
    return f"IMEI:{phone};STATUS:OK"
