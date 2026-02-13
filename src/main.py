import asyncio, time
import pandas as pd
from pathlib import Path

from .core.logger import setup_logger
from .core.validator import validate_devices
from .core.commands import COMMANDS
from .storage.excel import load_devices, save_devices

from .services.sms_service import SMSService
from .services.metrics import Metrics
from .services.queue_manager import process_devices


# Configuración
EXCEL_PATH = Path(__file__).resolve().parents[1] / "data" / "localizadores.xlsx"

NUM_WORKERS = 3
MAX_CONCURRENT_SMS = 1


async def async_main():
    
    # Empezamos a contar el tiempo
    start_time = time.perf_counter()

    logger = setup_logger()
    logger.info("==========INICIO DE EJECUCIÓN==========")
    logger.info("Iniciando procesamiento de dispositivos")

    # Cargar Excel
    df = load_devices(EXCEL_PATH)

    # Limpiar columna Estado y Error
    df["Estado"] = pd.Series(dtype="string")
    df["Error"] = pd.Series(dtype="string")


    # Crear métricas
    metrics = Metrics()

    # Validar dispositivos
    valid_indexes, invalid_devices = validate_devices(df, COMMANDS)

    # Marcar no soportados desde validación
    for index, error_message in invalid_devices:
        metrics.unsupported += 1
        df.at[index, "Estado"] = "NO SOPORTADO"
        df.at[index, "Error"] = error_message

    logger.info(f"{len(valid_indexes)} dispositivos válidos")
    logger.warning(f"{metrics.unsupported} dispositivos no soportados")

    # Crear servicio SMS
    sms_service = SMSService(
        retries=3,
        delay=5,
        timeout=10
    )

    # Procesar dispositivos en paralelo
    await process_devices(
        df=df,
        valid_indexes=valid_indexes,
        sms_service=sms_service,
        metrics=metrics,
        max_concurrent_sms=MAX_CONCURRENT_SMS,
        num_workers=NUM_WORKERS
    )

    # Guardar Excel actualizado
    save_devices(df, EXCEL_PATH)
    
    # Dejamos de contar el tiempo
    end_time = time.perf_counter()

    # Calcular métricas de tiempo
    total_time = end_time - start_time
    total_processed = len(valid_indexes)

    throughput = total_processed / total_time if total_time > 0 else 0
    avg_time = total_time / total_processed if total_processed > 0 else 0

    # Mostrar métricas finales
    summary = metrics.summary()
    print("\n")
    logger.info("----- RESUMEN FINAL -----")
    logger.info(f"Exitosos: {summary['success']}")
    logger.info(f"Errores: {summary['errors']}")
    logger.info(f"No soportados: {summary['unsupported']}")
    logger.info(f"Inoperativos: {summary['inoperative']}")
    logger.info(f"Tasa éxito: {summary['success_rate']}%")    
    logger.info("----- MÉTRICAS DE RENDIMIENTO -----")
    logger.info(f"Tiempo total: {total_time:.2f} segundos")
    logger.info(f"Tiempo promedio por dispositivo: {avg_time:.2f} segundos")
    logger.info(f"Throughput: {throughput:.2f} dispositivos/segundo")
    logger.info(f"Workers utilizados: {NUM_WORKERS}")                       
    logger.info("============FIN DE EJECUCIÓN===========")


if __name__ == "__main__":
    asyncio.run(async_main())
