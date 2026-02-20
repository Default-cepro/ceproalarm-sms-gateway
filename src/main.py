# src/main.py
import asyncio
import time
from pathlib import Path

import uvicorn

from .core.logger import setup_logger
from .core.validator import validate_devices
from .core.commands import COMMANDS
from .storage.excel import load_devices, save_devices

# IMPORTA el módulo server para usar las colas y send_command_and_wait (compartidas)
# Esto debe importarse *antes* de arrancar el servidor programáticamente.
from .api import server as server_module

from .services.sms_service import SMSService
from .services.metrics import Metrics
from .services.queue_manager import process_devices

# Configuración
EXCEL_PATH = Path(__file__).resolve().parents[1] / "data" / "localizadores.xlsx"

NUM_WORKERS = 1
MAX_CONCURRENT_SMS = 1


async def start_uvicorn_in_background(app_obj, host="0.0.0.0", port=80):
    """
    Arranca uvicorn programáticamente en el mismo loop async como tarea.
    Devuelve la instancia Server y la tarea.
    """
    config = uvicorn.Config(app=app_obj, host=host, port=port, log_level="info")
    server = uvicorn.Server(config=config)
    # server.serve() es una coroutine que ejecuta el server; la lanzamos como tarea
    server_task = asyncio.create_task(server.serve())
    # esperar un breve momento para que el server inicialice
    await asyncio.sleep(1)
    return server, server_task


async def async_main():
    # Empezamos a contar el tiempo
    start_time = time.perf_counter()

    logger = setup_logger()
    logger.info("==========INICIO DE EJECUCIÓN==========")
    logger.info("Iniciando procesamiento de dispositivos")

    # -------------- Arrancar el servidor (compartir colas/futuros) --------------
    # IMPORTANTE: arrancamos el servidor en el *mismo* loop para que
    # las asyncio.Queue y futures definidas en server_module funcionen con SMSService.
    uvicorn_host = "0.0.0.0"
    uvicorn_port = 80

    logger.info(f"Arrancando FastAPI (uvicorn) en {uvicorn_host}:{uvicorn_port} (background)...")
    server, server_task = await start_uvicorn_in_background(server_module.app, host=uvicorn_host, port=uvicorn_port)

    # ------------------- ESPERAR PRIMER LLAMADO DE LA APP -------------------
    # El servidor expondrá `first_request_event` (asyncio.Event) en server_module.
    # Aquí esperamos que la app (teléfono) haga la primera llamada (registro o poll)
    # antes de iniciar los workers/procesamiento. Si pasa timeout_seconds procedemos
    # (pero queda registrado en el log).
    timeout_seconds = 300  # segundos
    try:
        if hasattr(server_module, "first_request_event"):
            if timeout_seconds and timeout_seconds > 0:
                logger.info(f"Esperando primer llamado de la app (timeout={timeout_seconds}s)...")
                await asyncio.wait_for(server_module.first_request_event.wait(), timeout=timeout_seconds)
            else:
                logger.info("Esperando primer llamado de la app (sin timeout)...")
                await server_module.first_request_event.wait()
            logger.info("Primer llamado recibido: arrancando workers y procesamiento.")
        else:
            logger.warning("server_module.first_request_event no existe — procediendo sin espera.")
    except asyncio.TimeoutError:
        logger.warning(f"No se recibió primer llamado en {timeout_seconds}s — procediendo según configuración.")
    except Exception as ex:
        logger.exception("Error esperando primer llamado de la app: %s", ex)

    # Cargar Excel
    df = load_devices(EXCEL_PATH)

    # Limpiar columna Estado y Error si no existen
    if "Estado" not in df.columns:
        df["Estado"] = ""
    if "Error" not in df.columns:
        df["Error"] = ""

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
        retries=1,
        delay=30,
        timeout=30
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

    # -------------- Apagado del server uvicorn --------------
    logger.info("Deteniendo servidor uvicorn...")
    # Solicitar salida limpia del servidor
    server.should_exit = True
    # Esperar que la tarea termine (timeout opcional)
    try:
        await asyncio.wait_for(server_task, timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("uvicorn no terminó en 10s, cancelando tarea...")
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)

    logger.info("Servidor detenido. Fin del proceso.")


if __name__ == "__main__":
    asyncio.run(async_main())