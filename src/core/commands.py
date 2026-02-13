import json
from pathlib import Path


CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "commands.json"


def load_commands():
    with open(CONFIG_PATH, "r", encoding="utf-8") as file:
        raw_commands = json.load(file)

    # Normalizar todo a lowercase
    normalized_commands = {
        brand.strip().lower(): {
            model.strip().lower(): {
                action.strip().lower(): config
                for action, config in actions.items()
            }
            for model, actions in models.items()
        }
        for brand, models in raw_commands.items()
    }

    return normalized_commands


COMMANDS = load_commands()


def get_command(brand: str, model: str, action: str = "status") -> dict:
    brand = brand.strip().lower()
    model = model.strip().lower()
    action = action.strip().lower()

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
