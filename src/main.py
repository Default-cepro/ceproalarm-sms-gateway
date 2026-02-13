from .storage.excel import load_devices, save_devices
from .core.commands import get_command, COMMANDS
from .core.parser import parse_response
from .core.validator import validate_devices
from .core.logger import setup_logger
from .sms.simulator import send_sms
import numpy as np
import time

logger = setup_logger()

EXCEL_PATH = "data/localizadores.xlsx"


def main():
    start_time = time.time()

    df = load_devices(EXCEL_PATH)
    total_devices = len(df)

    valid_indexes, invalid_devices = validate_devices(df, COMMANDS)

    execution_errors = 0
    unsupported_counter = 0
    success_counter = 0
    inoperative_counter = 0

    logger.info("==== INICIO DE EJECUCIÓN ====")
    logger.info(f"Total dispositivos en Excel: {total_devices}")

    # ---------------------------
    # VALIDACIÓN
    # ---------------------------
    for index, error_message in invalid_devices:
        unsupported_counter += 1

        df.at[index, "Error"] = np.nan
        df.at[index, "Estado"] = "NO SOPORTADO"
        df.at[index, "Error"] = error_message

        logger.warning(error_message)

    print("\n")
    logger.info(f"\nDispositivos válidos: {len(valid_indexes)}")
    logger.info(f"No soportados: {unsupported_counter}\n")

    # ---------------------------
    # PROCESAMIENTO
    # ---------------------------
    processing_start = time.time()

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
            if status == "INOPERATIVO":
                inoperative_counter += 1

            df.at[index, "Estado"] = status

            success_counter += 1

            logger.success(
                f"0{phone} {brand} {model} actualizado a {status}"
            )
            print("\n")

        except Exception as e:
            execution_errors += 1

            df.at[index, "Estado"] = "ERROR"
            df.at[index, "Error"] = str(e)

            logger.error(
                f"Error con 0{phone} {brand} {model}: {e}"
            )
            print("\n")

    processing_end = time.time()

    # ---------------------------
    # MÉTRICAS
    # ---------------------------
    total_time = time.time() - start_time
    processing_time = processing_end - processing_start

    processed_devices = len(valid_indexes)
    attempted_devices = processed_devices
    effective_devices = total_devices - unsupported_counter

    success_rate = (
        (success_counter / effective_devices) * 100
        if effective_devices > 0 else 0
    )

    avg_time_per_device = (
        processing_time / attempted_devices
        if attempted_devices > 0 else 0
    )

    logger.info("==== RESUMEN DE EJECUCIÓN ====")
    logger.info(f"Tiempo total ejecución: {total_time:.2f} segundos")
    logger.info(f"Tiempo procesamiento SMS: {processing_time:.2f} segundos")
    logger.info(f"Exitosos: {success_counter}")
    logger.info(f"Dispositivos inoperativos: {inoperative_counter}")
    logger.info(f"Errores ejecución: {execution_errors}")
    logger.info(f"No soportados: {unsupported_counter}")
    logger.info(f"Porcentaje éxito: {success_rate:.2f}%")
    logger.info(f"Tiempo promedio por dispositivo: {avg_time_per_device:.2f} s")

    logger.info("==== FIN DE EJECUCIÓN ====")

    save_devices(df, EXCEL_PATH)


if __name__ == "__main__":
    main()
