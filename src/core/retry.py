import time
from loguru import logger
from ..sms.simulator import TimeoutException


def send_with_retry(send_function, retries=3, delay=5, *args):
    attempt = 0

    while attempt < retries:
        try:
            return send_function(*args)

        except TimeoutException as e:
            attempt += 1
            logger.warning(f"Timeout intento {attempt}/{retries}")

            if attempt >= retries:
                raise

            time.sleep(delay)
