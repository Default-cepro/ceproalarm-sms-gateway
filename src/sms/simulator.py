import time, random

prob = random.random()
def send_sms(brand: str, model: str, phone: str, message: str) -> str:
    print(f"Enviando SMS a {brand} {model} tlf: 0{phone}: {message}")
    
    time.sleep(2.5)
    prob = random.random()
    
#    if "STATUS" in message:
#            return f"IMEI:{phone};STATUS:OK"

    if "STATUS" in message and prob > 0.5:
        return f"IMEI:{phone};STATUS:OK"

    return "ERROR"