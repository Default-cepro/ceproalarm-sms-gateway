import json
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "commands.json"


def load_commands():
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        return json.load(file)


COMMANDS = load_commands()


def get_command(brand: str, model: str, action: str = "status") -> dict:
    brand = brand.lower()
    model = model.lower()

    try:
        return COMMANDS[brand][model][action]
    except KeyError:
        if brand not in COMMANDS:
            raise ValueError(f"Marca no soportada: {brand}")

        if model not in COMMANDS[brand]:
            raise ValueError(f"Modelo no soportado: {brand} {model}")

        if action not in COMMANDS[brand][model]:
            raise ValueError(
                f"Acción '{action}' no definida para {brand} {model}"
            )
