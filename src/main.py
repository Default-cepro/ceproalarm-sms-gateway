import time
from .gsm.fake import FakeGSM


def main():
    gsm = FakeGSM(response_delay=0.5)

    trackers = [
        {"phone": "+584121111111", "command": "imei123456789012345"},
        {"phone": "+584122222222", "command": "status"},
        {"phone": "+584123333333", "command": "reset"},
    ]

    print("=== Envío de comandos ===")
    for tracker in trackers:
        gsm.send_sms(tracker["phone"], tracker["command"])

    print("\n=== Escuchando respuestas ===")
    start_time = time.time()
    timeout = 5  # segundos

    while time.time() - start_time < timeout:
        sms = gsm.read_sms()
        if sms:
            phone, message = sms
            print(f"[RESPUESTA] {phone}: {message}")
        else:
            time.sleep(0.2)

    print("\nProceso finalizado.")


if __name__ == "__main__":
    main()
