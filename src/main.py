import asyncio
import errno
import os
import time
from datetime import datetime, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from .api.app import app
from .core.config import settings
from .core.logger import setup_logger
from .services.day_lifecycle import _run_single_batch
from .services.email_service import EmailReportService
from .services.persistence import RunPersistence
from .services.scheduler import _run_daily_scheduler
from .services.sms_service import SMSService
from .services.webhook_registry import register_cloud_webhooks, unregister_cloud_webhooks


def _find_bind_oserror(exc: BaseException | None) -> OSError | None:
    current = exc
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        if isinstance(current, OSError) and current.errno in {errno.EADDRINUSE, 98, 48}:
            return current
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return None


def _format_startup_error(host: str, port: int, exc: BaseException | None) -> str:
    bind_error = _find_bind_oserror(exc)
    if bind_error is not None:
        return (
            f"Puerto {port} ya está en uso en {host}. "
            f"Detén el proceso que lo ocupa (ej: `lsof -i :{port}`) "
            f"o cambia `SMS_GATE_SERVER_PORT`."
        )
    if exc is not None:
        return f"No se pudo iniciar Uvicorn en {host}:{port}: {exc}"
    return f"Uvicorn terminó durante startup en {host}:{port} sin excepción"


def _resolve_runtime_timezone(logger) -> tzinfo:
    tz_name = settings.timezone
    if tz_name:
        try:
            tz = ZoneInfo(tz_name)
            logger.info(f"Zona horaria del scheduler: {tz_name}")
            return tz
        except Exception as ex:
            logger.warning(f"No se pudo usar SMS_GATE_TIMEZONE='{tz_name}': {ex}. Se usará zona local.")
    tz = datetime.now().astimezone().tzinfo
    if tz is None:
        tz = ZoneInfo("UTC")
    logger.info(f"Zona horaria local detectada: {tz}")
    return tz


async def start_uvicorn_in_background(app_obj, host="0.0.0.0", port=8000, access_log: bool = False):
    """
    Arranca uvicorn programáticamente en el mismo loop async como tarea.
    Devuelve la instancia Server y la tarea.
    """
    config = uvicorn.Config(app=app_obj, host=host, port=port, log_level="info", access_log=access_log)
    server = uvicorn.Server(config=config)

    async def _serve_with_guard() -> BaseException | None:
        try:
            await server.serve()
            return None
        except BaseException as ex:
            return ex

    server_task = asyncio.create_task(_serve_with_guard())

    startup_wait_seconds = 1.5
    started_at = time.perf_counter()
    while (time.perf_counter() - started_at) < startup_wait_seconds:
        if server_task.done():
            try:
                outcome = server_task.result()
            except BaseException as ex:
                outcome = ex
            exc = outcome if isinstance(outcome, BaseException) else None
            message = _format_startup_error(host, port, exc)
            raise RuntimeError(message) from exc
        await asyncio.sleep(0.05)

    return server, server_task


async def async_main():
    start_time = time.perf_counter()

    logger = setup_logger()
    logger.info("========== INICIO DEL SERVICIO ==========")

    uvicorn_host = settings.server_host
    uvicorn_port = settings.server_port

    if hasattr(os, "geteuid") and uvicorn_port < 1024:
        try:
            if os.geteuid() != 0:
                logger.error(
                    "Puerto {} requiere privilegios en Linux. "
                    "Configura SMS_GATE_SERVER_PORT=8000 (o mayor) y vuelve a ejecutar.",
                    uvicorn_port,
                )
                raise SystemExit(2)
        except Exception:
            pass

    uvicorn_access_log = settings.access_log
    logger.info(
        f"Arrancando FastAPI (uvicorn) en {uvicorn_host}:{uvicorn_port} "
        f"(background, access_log={'ON' if uvicorn_access_log else 'OFF'})..."
    )
    try:
        server, server_task = await start_uvicorn_in_background(
            app,
            host=uvicorn_host,
            port=uvicorn_port,
            access_log=uvicorn_access_log,
        )
    except RuntimeError as ex:
        logger.error(str(ex))
        raise SystemExit(2)

    auto_register_webhooks = settings.auto_register_webhooks
    unregister_on_exit = settings.unregister_on_exit
    cloud_api_url = settings.api_url
    cloud_api_username = settings.api_username
    cloud_api_password = settings.api_password
    webhook_url = settings.webhook_url
    webhook_events = settings.webhook_events
    device_id = settings.device_id
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
                "Auto registro de webhooks activo pero faltan variables: " + ", ".join(missing_vars)
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
                    "(no usar login del flujo local API)."
                )

    if settings.local_api_enabled:
        logger.info("SMS_GATE_LOCAL_API_ENABLED=1 -> flujo local API directo (ADB/local server).")
        logger.info(
            "Local API base URL activa: {}",
            settings.local_api_base_url,
        )
    else:
        logger.warning(
            "SMS_GATE_LOCAL_API_ENABLED=0 -> el envío por polling fue eliminado; "
            "se requiere el flujo local API (ADB/local server)."
        )

    excel_paths = settings.excel_paths
    if not excel_paths:
        raise ValueError(
            "EXCEL_PATH no está definido o no encontró archivos Excel válidos. "
            "Puedes usar archivo, carpeta o glob (p.ej. data/lote/*.xlsx), "
            "separados por ';' o ','."
        )

    sms_service = SMSService(
        retries=settings.sms_retries,
        delay=settings.sms_retry_delay_seconds,
        timeout=settings.sms_timeout_seconds,
    )

    schedule_enabled = settings.schedule_enabled
    run_times = settings.daily_run_times
    skip_past_rounds = settings.skip_past_rounds
    skip_grace_seconds = settings.skip_grace_seconds
    offline_alert_recipients = settings.offline_alert_recipients
    email_report_enabled = settings.email_report_enabled
    email_report_recipients = settings.email_report_recipients
    email_subject_prefix = settings.email_report_subject_prefix
    email_service: EmailReportService | None = None
    if email_report_enabled:
        smtp_host = settings.email_smtp_host
        smtp_port = settings.email_smtp_port
        smtp_username = settings.email_smtp_username
        smtp_password = settings.email_smtp_password
        smtp_from = settings.email_from
        smtp_use_ssl = settings.email_smtp_use_ssl
        smtp_use_tls = settings.email_smtp_use_tls
        smtp_timeout_seconds = settings.email_smtp_timeout_seconds

        missing_email_vars = []
        if not smtp_host:
            missing_email_vars.append("EMAIL_SMTP_HOST")
        if not smtp_from:
            missing_email_vars.append("EMAIL_FROM (o EMAIL_SMTP_USERNAME)")

        if missing_email_vars:
            logger.warning(
                "Reporte por correo activo pero faltan variables: " + ", ".join(missing_email_vars)
            )
        else:
            email_service = EmailReportService(
                smtp_host=smtp_host,
                smtp_port=smtp_port,
                smtp_username=smtp_username,
                smtp_password=smtp_password,
                from_address=smtp_from,
                use_tls=smtp_use_tls,
                use_ssl=smtp_use_ssl,
                timeout_seconds=smtp_timeout_seconds,
            )
    runtime_tz = _resolve_runtime_timezone(logger)
    maintenance_flag_path = Path(settings.maintenance_flag_path) if settings.maintenance_flag_path else None
    maintenance_recheck_seconds = settings.maintenance_recheck_seconds
    persistence_enabled = settings.persistence_enabled
    persistence_path = Path(settings.persistence_path) if settings.persistence_path else None
    persistence: RunPersistence | None = None
    if persistence_enabled and persistence_path is not None:
        persistence = RunPersistence(persistence_path, logger)

    logger.info(
        f"Modo scheduler={'ON' if schedule_enabled else 'OFF'} | "
        f"horas={', '.join(t.strftime('%H:%M:%S') for t in run_times)} | "
        f"skip_pasadas={'1' if skip_past_rounds else '0'}"
    )
    if skip_past_rounds:
        logger.info(f"Ventana de gracia skip_pasadas: {skip_grace_seconds}s")
    if maintenance_flag_path:
        logger.info(f"Mantenimiento por bandera de archivo: {maintenance_flag_path}")
    logger.info(f"Destinatarios alerta OFFLINE: {offline_alert_recipients or ['(sin configurar)']}")
    logger.info(f"Reporte por correo={'ON' if email_report_enabled else 'OFF'}")
    if email_report_enabled:
        logger.info(f"Destinatarios correo: {email_report_recipients or ['(sin configurar)']}")
    logger.info(f"Persistencia={'ON' if persistence is not None else 'OFF'}")
    if persistence is not None:
        logger.info(f"Archivo persistencia: {persistence_path}")

    try:
        if schedule_enabled:
            await _run_daily_scheduler(
                excel_paths=excel_paths,
                sms_service=sms_service,
                run_times=run_times,
                skip_past_rounds=skip_past_rounds,
                skip_grace_seconds=skip_grace_seconds,
                offline_alert_recipients=offline_alert_recipients,
                email_service=email_service,
                email_report_recipients=email_report_recipients,
                email_subject_prefix=email_subject_prefix,
                runtime_tz=runtime_tz,
                maintenance_flag_path=maintenance_flag_path,
                maintenance_recheck_seconds=maintenance_recheck_seconds,
                logger=logger,
                persistence=persistence,
            )
        else:
            await _run_single_batch(
                excel_paths=excel_paths,
                sms_service=sms_service,
                offline_alert_recipients=offline_alert_recipients,
                email_service=email_service,
                email_report_recipients=email_report_recipients,
                email_subject_prefix=email_subject_prefix,
                runtime_tz=runtime_tz,
                logger=logger,
                persistence=persistence,
            )
    finally:
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

        server.should_exit = True
        try:
            await asyncio.wait_for(server_task, timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("uvicorn no terminó en 10s, cancelando tarea...")
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)

        total_time = time.perf_counter() - start_time
        logger.info(f"Servidor detenido. Uptime total: {total_time:.2f} segundos")
        logger.info("========== FIN DEL SERVICIO ==========")


if __name__ == "__main__":
    asyncio.run(async_main())