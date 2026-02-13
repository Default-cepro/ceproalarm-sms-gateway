from .storage.excel import load_devices, save_devices
from .core.commands import get_command, COMMANDS
from .core.parser import parse_response
from .core.validator import validate_devices
from .core.logger import setup_logger
from .sms.simulator import send_sms
import numpy as np

logger = setup_logger()

EXCEL_PATH = "data/localizadores.xlsx"


def main():
    df = load_devices(EXCEL_PATH)
    valid_indexes, invalid_devices = validate_devices(df, COMMANDS)

    Ecounter = 0
    Invcounter = 0

    logger.info("Iniciando validación de dispositivos")
    print("\n")


    # Marcar inválidos en Excel
    for index, error_message in invalid_devices:
        Invcounter += 1

        df.at[index, "Error"] = np.nan
        df.at[index, "Estado"] = "NO SOPORTADO"
        df.at[index, "Error"] = error_message

        logger.warning(f"{error_message}")
    
    print("\n")

    logger.info("Validación completada")
    logger.info(f"Dispositivos válidos: {len(valid_indexes)}")
    logger.info(f"Dispositivos no soportados: {Invcounter}")

    # Procesar válidos
    for index in valid_indexes:

        row = df.loc[index]

        phone = str(row["Teléfono"])
        brand = str(row["Marca"])
        model = str(row["Modelo"])

        logger.info(f"Procesando 0{phone} | {brand} {model}")

        try:
            command_data = get_command(brand, model)

            response = send_sms(
                brand,
                model,
                phone,
                command_data["command"]
            )

            status = parse_response(
                response,
                command_data["expected"]
            )

            df.at[index, "Estado"] = status

            logger.success(
                f"0{phone} {brand} {model} actualizado a {status}"
            )
            print("\n")


        except Exception as e:
            Ecounter += 1

            df.at[index, "Estado"] = "ERROR"
            df.at[index, "Error"] = str(e)

            logger.error(
                f"Error con 0{phone} {brand} {model}: {e}"
            )
    logger.info("Proceso finalizado")
    logger.info(f"Inconvenientes de ejecución: {Ecounter}")
    logger.info(f"Comandos inválidos: {Invcounter}")

    save_devices(df, EXCEL_PATH)


if __name__ == "__main__":
    main()
