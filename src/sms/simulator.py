import random
import time


class TimeoutException(Exception):
    pass


def send_sms(phone: str, message: str) -> str:
    print(f"Enviando SMS a {phone}: {message}")

    # Simular latencia real
    time.sleep(random.uniform(0.5, 2.0))

    probability = random.random()

    # 20% timeout
    if probability < 0.2:
        raise TimeoutException("Timeout")

    # 10% error raro
    if probability < 0.3:
        return "ERROR"

    # 70% éxito
    return f"IMEI:{phone};STATUS:OK"
