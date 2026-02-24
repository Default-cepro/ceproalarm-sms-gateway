import asyncio
import time
import os
from dotenv import load_dotenv
from pathlib import Path

import uvicorn

from .core.logger import setup_logger
from .core.validator import validate_devices
from .core.commands import COMMANDS
from .storage.excel import load_devices, save_devices

from .api import server as server_module

from .services.sms_service import SMSService
from .services.metrics import Metrics
from .services.queue_manager import process_devices
from .services.webhook_registry import register_cloud_webhooks, unregister_cloud_webhooks

env_var = Path(__file__).resolve().parents[1] /".env"
load_dotenv(env_var)
EXCEL_PATH = os.getenv("EXCEL_PATH")

NUM_WORKERS = 1
MAX_CONCURRENT_SMS = 1


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_events(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [p for p in parts if p]


async def start_uvicorn_in_background(app_obj, host="0.0.0.0", port=80):
    """
    Arranca uvicorn programáticamente en el mismo loop async como tarea.
    Devuelve la instancia Server y la tarea.
    """
    config = uvicorn.Config(app=app_obj, host=host, port=port, log_level="info")
    server = uvicorn.Server(config=config)
    # server.serve() as a co-task
    server_task = asyncio.create_task(server.serve())
    # wait for the server to initialize 
    await asyncio.sleep(1)
    return server, server_task


async def async_main():
    # Started counting time 
    start_time = time.perf_counter()

    logger = setup_logger()
    logger.info("==========INICIO DE EJECUCIÓN==========")
    logger.info("Iniciando procesamiento de dispositivos")

    # -------------- BOOT THE SERVER --------------
    uvicorn_host = "0.0.0.0"
    uvicorn_port = 80

    logger.info(f"Arrancando FastAPI (uvicorn) en {uvicorn_host}:{uvicorn_port} (background)...")
    server, server_task = await start_uvicorn_in_background(server_module.app, host=uvicorn_host, port=uvicorn_port)

    # ------------------- Optional register of webhooks Cloud -------------------
    auto_register_webhooks = _env_bool("SMS_GATE_AUTO_REGISTER_WEBHOOKS", default=False)
    unregister_on_exit = _env_bool("SMS_GATE_UNREGISTER_ON_EXIT", default=False)
    cloud_api_url = os.getenv("SMS_GATE_API_URL", "https://api.sms-gate.app/3rdparty/v1").strip()
    cloud_api_username = os.getenv("SMS_GATE_API_USERNAME", "").strip()
    cloud_api_password = os.getenv("SMS_GATE_API_PASSWORD", "")
    webhook_url = os.getenv("SMS_GATE_WEBHOOK_URL", "").strip()
    webhook_events = _env_events(
        "SMS_GATE_WEBHOOK_EVENTS",
        "sms:received,sms:sent,sms:delivered,sms:failed"
    )
    device_id = os.getenv("SMS_GATE_DEVICE_ID", "").strip() or None
    registered_webhook_ids: list[str] = []

    if auto_register_webhooks:
        missing_vars = []
        if not cloud_api_username:
            missing_vars.append("SMS_GATE_API_USERNAME")
        if not cloud_api_password:
            missing_vars.append("SMS_GATE_API_PASSWORD")
        if not webhook_url:
            missing_vars.append("SMS_GATE_WEBHOOK_URL")

        if missing_vars:
            logger.warning(
                "Auto registro de webhooks activo pero faltan variables: "
                + ", ".join(missing_vars)
            )
        else:
            logger.info(
                f"Registrando webhooks Cloud en {cloud_api_url} -> {webhook_url} "
                f"(events={webhook_events}, device_id={device_id or 'ALL'})"
            )
            ok, errors = await register_cloud_webhooks(
                api_url=cloud_api_url,
                username=cloud_api_username,
                password=cloud_api_password,
                webhook_url=webhook_url,
                events=webhook_events,
                device_id=device_id,
            )
            for it in ok:
                logger.info(f"Webhook registrado: event={it.get('event')} id={it.get('id')}")
                if it.get("id"):
                    registered_webhook_ids.append(str(it["id"]))
            for err in errors:
                logger.error(
                    "Error registrando webhook: event={event} status={status} detail={detail}".format(
                        event=err.get("event"),
                        status=err.get("status_code"),
                        detail=err.get("message"),
                    )
                )
            if errors and all(err.get("status_code") == 401 for err in errors):
                logger.error(
                    "Cloud API respondió 401. Verifica usuario/contraseña de API en Home tab "
                    "(no usar login de /webhook/sms/device)."
                )

    # ------------------- WAIT FOR FIRST APP CALL -------------------
    # The server will expose `first_request_event` (asyncio.Event) in server_module.
    # Here we wait for the app (phone) do the first call (register o poll)
    # before starting workers/processing. If timeout_seconds pass we proceed
    # (but it is recorded in the log).
    local_api_mode = _env_bool("SMS_GATE_LOCAL_API_ENABLED", default=False)
    if local_api_mode:
        logger.info("SMS_GATE_LOCAL_API_ENABLED=1 -> omitiendo espera de primer llamado (/device|/message polling).")
    else:
        timeout_seconds = 300 
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

    # Load excel
    df = load_devices(EXCEL_PATH)

    # Clean up "Estado" and "Error" columns in each execution
    if "Estado" not in df.columns:
        df["Estado"] = ""
    else:
        df["Estado"] = ""

    if "Error" not in df.columns:
        df["Error"] = ""
    else:
        df["Error"] = ""

    # Make the metrics
    metrics = Metrics()

    # Validate devices
    valid_indexes, invalid_devices = validate_devices(df, COMMANDS)

    # Mark unsupported from validation
    for index, error_message in invalid_devices:
        metrics.unsupported += 1
        df.at[index, "Estado"] = "NO SOPORTADO"
        df.at[index, "Error"] = error_message

    logger.info(f"{len(valid_indexes)} dispositivos válidos")
    logger.warning(f"{metrics.unsupported} dispositivos no soportados")

    # Make a SMS service
    sms_service = SMSService(
        retries=1,
        delay=30,
        timeout=20
    )

    # Process divices in parallel 
    await process_devices(
        df=df,
        valid_indexes=valid_indexes,
        sms_service=sms_service,
        metrics=metrics,
        max_concurrent_sms=MAX_CONCURRENT_SMS,
        num_workers=NUM_WORKERS
    )

    # Save Updated Excel
    save_devices(df, EXCEL_PATH)

    # Stop counting the time
    end_time = time.perf_counter()

    # We calculate time metrics
    total_time = end_time - start_time
    total_processed = len(valid_indexes)

    throughput = total_processed / total_time if total_time > 0 else 0
    avg_time = total_time / total_processed if total_processed > 0 else 0

    # Show final metrics
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

    # -------------- Shut down uvicorn server --------------
    logger.info("Deteniendo servidor uvicorn...")

    if auto_register_webhooks and unregister_on_exit and registered_webhook_ids:
        logger.info(f"Deregistrando {len(registered_webhook_ids)} webhooks Cloud...")
        unregister_errors = await unregister_cloud_webhooks(
            api_url=cloud_api_url,
            username=cloud_api_username,
            password=cloud_api_password,
            webhook_ids=registered_webhook_ids,
        )
        for err in unregister_errors:
            logger.warning(
                "Error al eliminar webhook id={id} status={status} detail={detail}".format(
                    id=err.get("id"),
                    status=err.get("status_code"),
                    detail=err.get("message"),
                )
            )

    # Request clean exit from server
    server.should_exit = True
    # Wait for the task to finish (optional timeout)
    try:
        await asyncio.wait_for(server_task, timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("uvicorn no terminó en 10s, cancelando tarea...")
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)

    logger.info("Servidor detenido. Fin del proceso.")


if __name__ == "__main__":
    asyncio.run(async_main())
