import asyncio
import errno
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import uvicorn
from dotenv import load_dotenv

from .api import server as server_module
from .core.commands import COMMANDS
from .core.logger import setup_logger
from .core.validator import validate_devices
from .services.metrics import Metrics
from .services.queue_manager import process_devices
from .services.sms_service import SMSService
from .services.webhook_registry import register_cloud_webhooks, unregister_cloud_webhooks
from .storage.excel import load_devices, save_devices

env_var = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(env_var)

NUM_WORKERS = 1
MAX_CONCURRENT_SMS = 1
STATUS_PRIORITY = {"OFFLINE": 0, "UNKNOWN": 1, "ONLINE": 2}


@dataclass
class DeviceAggregate:
    status: str = "OFFLINE"
    error: str = ""
    rounds_observed: int = 0


@dataclass
class DailyExcelState:
    path: str
    base_df: Any
    valid_indexes: list[Any]
    invalid_devices: list[tuple[Any, str]]
    aggregate: dict[Any, DeviceAggregate] = field(default_factory=dict)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, min_value: int = 0, max_value: int = 65535) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    if value < min_value:
        return min_value
    if value > max_value:
        return max_value
    return value


def _env_events(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [p for p in parts if p]


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    if raw is None:
        return []
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [p for p in parts if p]


def _normalize_excel_path(value: str) -> str:
    if not value:
        return value
    cleaned = value.strip().strip('"').strip("'")
    cleaned = os.path.expandvars(os.path.expanduser(cleaned))

    if os.name != "nt":
        match = re.match(r"^([A-Za-z]):[\\/](.*)$", cleaned)
        if match:
            drive = match.group(1).lower()
            rest = match.group(2).replace("\\", "/")
            return f"/mnt/{drive}/{rest}"
        cleaned = cleaned.replace("\\", "/")

    return cleaned


def _parse_excel_paths(raw_value: str) -> list[str]:
    if not raw_value:
        return []
    parts = [p.strip() for p in re.split(r"[;,]", raw_value) if p.strip()]
    return [_normalize_excel_path(p) for p in parts]


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


def _parse_daily_run_times(raw_value: str) -> list[dt_time]:
    fallback = "08:00,14:00,20:00"
    raw = (raw_value or fallback).strip()
    tokens = [p.strip() for p in raw.replace(";", ",").split(",") if p.strip()]
    if not tokens:
        tokens = [p.strip() for p in fallback.split(",") if p.strip()]

    parsed_times: list[dt_time] = []
    for token in tokens:
        parsed = None
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(token, fmt).time().replace(microsecond=0)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"Hora inválida en SMS_GATE_DAILY_RUN_TIMES: '{token}'. Usa HH:MM o HH:MM:SS")
        parsed_times.append(parsed)

    unique_times = sorted(set(parsed_times))
    if not unique_times:
        raise ValueError("SMS_GATE_DAILY_RUN_TIMES no tiene horarios válidos.")
    return unique_times


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status in ("ONLINE", "UNKNOWN", "OFFLINE"):
        return status
    return "OFFLINE"


def _merge_status(current_status: str, round_status: str) -> str:
    current = _normalize_status(current_status)
    new_value = _normalize_status(round_status)
    if STATUS_PRIORITY[new_value] > STATUS_PRIORITY[current]:
        return new_value
    return current


def _resolve_runtime_timezone(logger) -> tzinfo:
    tz_name = os.getenv("SMS_GATE_TIMEZONE", "").strip()
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


async def _sleep_until(target_dt: datetime):
    while True:
        now = datetime.now(target_dt.tzinfo)
        remaining = (target_dt - now).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 30))


def _prepare_daily_excel_states(excel_paths: list[str], logger) -> list[DailyExcelState]:
    states: list[DailyExcelState] = []
    for excel_path in excel_paths:
        logger.info(f"Cargando archivo Excel para jornada: {excel_path}")
        try:
            df = load_devices(excel_path, commands_config=COMMANDS)
        except Exception as ex:
            logger.exception(f"No se pudo cargar Excel ({excel_path}): {ex}")
            continue

        if df.empty:
            logger.warning(f"No se encontraron filas válidas con encabezados esperados en: {excel_path}")
            continue

        if "Status" not in df.columns:
            df["Status"] = ""
        if "Error" not in df.columns:
            df["Error"] = ""

        valid_indexes, invalid_devices = validate_devices(df, COMMANDS)
        normalized_invalid = [(idx, str(error_message)) for idx, error_message in invalid_devices]

        aggregate: dict[Any, DeviceAggregate] = {idx: DeviceAggregate() for idx in valid_indexes}
        for idx, error_message in normalized_invalid:
            aggregate[idx] = DeviceAggregate(status="UNKNOWN", error=error_message, rounds_observed=0)

        states.append(
            DailyExcelState(
                path=excel_path,
                base_df=df,
                valid_indexes=list(valid_indexes),
                invalid_devices=normalized_invalid,
                aggregate=aggregate,
            )
        )
        logger.info(
            f"Jornada inicializada para {excel_path}: "
            f"válidos={len(valid_indexes)} no_soportados={len(normalized_invalid)}"
        )
    return states


async def _execute_round_for_day(
    day_states: list[DailyExcelState],
    sms_service: SMSService,
    round_number: int,
    total_rounds: int,
    logger,
):
    logger.info(f"========== RONDA {round_number}/{total_rounds} ==========")
    for state in day_states:
        round_df = state.base_df.copy(deep=True)
        round_df["Status"] = ""
        if "Error" not in round_df.columns:
            round_df["Error"] = ""
        else:
            round_df["Error"] = ""

        round_metrics = Metrics()

        for idx, error_message in state.invalid_devices:
            round_metrics.unsupported += 1
            round_df.at[idx, "Status"] = "UNKNOWN"
            round_df.at[idx, "Error"] = error_message
            logger.warning(f"Fila no soportada ({state.path}): {error_message}")

        if state.valid_indexes:
            await process_devices(
                df=round_df,
                valid_indexes=state.valid_indexes,
                sms_service=sms_service,
                metrics=round_metrics,
                max_concurrent_sms=MAX_CONCURRENT_SMS,
                num_workers=NUM_WORKERS,
            )

        counts = {"ONLINE": 0, "UNKNOWN": 0, "OFFLINE": 0}
        for idx in state.valid_indexes:
            round_status = _normalize_status(round_df.at[idx, "Status"])
            round_error = str(round_df.at[idx, "Error"] or "").strip()

            aggregate = state.aggregate.setdefault(idx, DeviceAggregate())
            aggregate.rounds_observed += 1
            aggregate.status = _merge_status(aggregate.status, round_status)

            if aggregate.status in ("ONLINE", "UNKNOWN"):
                aggregate.error = ""
            elif round_status == "OFFLINE":
                aggregate.error = round_error or aggregate.error or "NO_RESPONSE_TIMEOUT"

            counts[round_status] += 1

        summary = round_metrics.summary()
        logger.info(
            f"Ronda {round_number} ({state.path}) -> "
            f"ONLINE={counts['ONLINE']} UNKNOWN={counts['UNKNOWN']} OFFLINE={counts['OFFLINE']} "
            f"errores={summary['errors']} no_soportados={summary['unsupported']}"
        )


def _build_offline_alert_messages(day_label: str, offline_devices: list[dict[str, str]], max_chars: int = 150) -> list[str]:
    total = len(offline_devices)
    summary = f"CEPROALARM {day_label}: {total} localizadores OFFLINE al cierre."
    if total == 0:
        return [summary]

    numbers = [str(it.get("phone", "")).strip() or "SIN_NUMERO" for it in offline_devices]
    details: list[str] = []
    prefix = "OFFLINE: "
    current = prefix
    for number in numbers:
        sep = "" if current == prefix else ", "
        candidate = f"{current}{sep}{number}"
        if len(candidate) > max_chars and current != prefix:
            details.append(current)
            current = f"{prefix}{number}"
        else:
            current = candidate
    if current != prefix:
        details.append(current)
    return [summary] + details


async def _notify_offline_devices(
    day_label: str,
    offline_devices: list[dict[str, str]],
    sms_service: SMSService,
    recipients: list[str],
    logger,
):
    if not recipients:
        logger.info("SMS_GATE_OFFLINE_ALERT_RECIPIENTS vacío. No se enviarán alertas OFFLINE.")
        return
    if not offline_devices:
        logger.info("Sin localizadores OFFLINE al cierre. No se envían alertas.")
        return

    messages = _build_offline_alert_messages(day_label, offline_devices)
    for recipient in recipients:
        for message in messages:
            try:
                result = await sms_service.send_notification(recipient, message)
                logger.info(
                    f"Alerta OFFLINE enviada a {recipient} "
                    f"(status={result.get('status', 'unknown')}, id={result.get('message_id', 'n/a')})"
                )
            except Exception as ex:
                logger.error(f"No se pudo enviar alerta OFFLINE a {recipient}: {ex}")
            await asyncio.sleep(0.2)


async def _finalize_day(
    day_date: date,
    day_states: list[DailyExcelState],
    sms_service: SMSService,
    offline_alert_recipients: list[str],
    logger,
):
    day_label = day_date.isoformat()
    logger.info(f"========== CIERRE DE JORNADA {day_label} ==========")

    offline_devices: list[dict[str, str]] = []
    for state in day_states:
        output_df = state.base_df.copy(deep=True)

        for idx in state.valid_indexes:
            aggregate = state.aggregate.get(idx, DeviceAggregate())
            final_status = _normalize_status(aggregate.status)
            final_error = ""
            if final_status == "OFFLINE":
                final_error = aggregate.error or "NO_RESPONSE_TIMEOUT"
                row = output_df.loc[idx]
                offline_devices.append(
                    {
                        "phone": str(row.get("Telefono", "")).strip(),
                        "brand": str(row.get("Marca", "")).strip(),
                        "model": str(row.get("Modelo", "")).strip(),
                        "excel_path": state.path,
                    }
                )

            output_df.at[idx, "Status"] = final_status
            if "Error" in output_df.columns:
                output_df.at[idx, "Error"] = final_error

        for idx, error_message in state.invalid_devices:
            output_df.at[idx, "Status"] = "UNKNOWN"
            if "Error" in output_df.columns:
                output_df.at[idx, "Error"] = error_message

        try:
            save_devices(output_df, state.path)
            logger.info(f"Archivo Excel actualizado al cierre del día: {state.path}")
        except PermissionError as ex:
            logger.error(
                "No se pudo guardar Excel por permisos en {}: {}. "
                "Revisa permisos del volumen/directorio data en Docker.",
                state.path,
                ex,
            )
        except Exception as ex:
            logger.exception(f"Error guardando Excel al cierre del día ({state.path}): {ex}")

    deduped_offline: list[dict[str, str]] = []
    seen_keys = set()
    for item in offline_devices:
        key = (item.get("excel_path", ""), item.get("phone", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped_offline.append(item)

    await _notify_offline_devices(
        day_label=day_label,
        offline_devices=deduped_offline,
        sms_service=sms_service,
        recipients=offline_alert_recipients,
        logger=logger,
    )
    logger.info(
        f"Cierre de jornada {day_label} completado. "
        f"Total OFFLINE finales={len(deduped_offline)}"
    )


async def _run_single_batch(
    excel_paths: list[str],
    sms_service: SMSService,
    offline_alert_recipients: list[str],
    runtime_tz: tzinfo,
    logger,
):
    logger.info("SMS_GATE_SCHEDULE_ENABLED=0 -> ejecución única")
    day_states = _prepare_daily_excel_states(excel_paths, logger)
    if not day_states:
        logger.warning("No hay Excel válidos para procesar en ejecución única.")
        return
    await _execute_round_for_day(
        day_states=day_states,
        sms_service=sms_service,
        round_number=1,
        total_rounds=1,
        logger=logger,
    )
    await _finalize_day(
        day_date=datetime.now(runtime_tz).date(),
        day_states=day_states,
        sms_service=sms_service,
        offline_alert_recipients=offline_alert_recipients,
        logger=logger,
    )


async def _run_daily_scheduler(
    excel_paths: list[str],
    sms_service: SMSService,
    run_times: list[dt_time],
    skip_past_rounds: bool,
    offline_alert_recipients: list[str],
    runtime_tz: tzinfo,
    maintenance_flag_path: Path | None,
    maintenance_recheck_seconds: int,
    logger,
):
    current_day: date | None = None
    day_states: list[DailyExcelState] = []
    next_round_index = 0
    day_finalized = False
    ran_any_round = False

    while True:
        now = datetime.now(runtime_tz)
        if current_day != now.date():
            current_day = now.date()
            day_states = _prepare_daily_excel_states(excel_paths, logger)
            next_round_index = 0
            day_finalized = False
            ran_any_round = False

            if skip_past_rounds:
                next_round_index = sum(
                    1
                    for run_time in run_times
                    if datetime.combine(current_day, run_time, tzinfo=runtime_tz) < now
                )
                if next_round_index > 0:
                    logger.warning(
                        f"Se omiten {next_round_index} ronda(s) ya vencidas de hoy "
                        f"(SMS_GATE_SKIP_PAST_ROUNDS=1)."
                    )

            logger.info(
                f"Nueva jornada {current_day.isoformat()} -> "
                f"rondas configuradas={len(run_times)} pendientes={max(len(run_times) - next_round_index, 0)}"
            )

        if current_day is None:
            await asyncio.sleep(1)
            continue
        active_day = current_day

        if next_round_index >= len(run_times):
            if not day_finalized and ran_any_round:
                await _finalize_day(
                    day_date=active_day,
                    day_states=day_states,
                    sms_service=sms_service,
                    offline_alert_recipients=offline_alert_recipients,
                    logger=logger,
                )
                day_finalized = True

            next_day = active_day + timedelta(days=1)
            next_target = datetime.combine(next_day, run_times[0], tzinfo=runtime_tz)
            logger.info(f"Esperando próxima jornada: {next_target.isoformat()}")
            await _sleep_until(next_target)
            continue

        target_dt = datetime.combine(active_day, run_times[next_round_index], tzinfo=runtime_tz)
        now = datetime.now(runtime_tz)
        if now < target_dt:
            logger.info(
                f"Próxima ronda {next_round_index + 1}/{len(run_times)} programada para "
                f"{target_dt.strftime('%Y-%m-%d %H:%M:%S')}"
            )
            await _sleep_until(target_dt)
            continue

        if maintenance_flag_path and maintenance_flag_path.exists():
            logger.warning(
                f"Mantenimiento activo ({maintenance_flag_path}). "
                "Ronda pausada hasta retirar el archivo bandera."
            )
            while maintenance_flag_path.exists():
                await asyncio.sleep(maintenance_recheck_seconds)
            logger.info("Mantenimiento finalizado. Reanudando rondas.")
            continue

        if not day_states:
            logger.warning("No hay Excel válidos para procesar en esta ronda.")
        else:
            await _execute_round_for_day(
                day_states=day_states,
                sms_service=sms_service,
                round_number=next_round_index + 1,
                total_rounds=len(run_times),
                logger=logger,
            )
            ran_any_round = True

        next_round_index += 1


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

    uvicorn_host = os.getenv("SMS_GATE_SERVER_HOST", "0.0.0.0").strip() or "0.0.0.0"
    uvicorn_port = _env_int("SMS_GATE_SERVER_PORT", 8000, min_value=1, max_value=65535)

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

    uvicorn_access_log = _env_bool("SMS_GATE_ACCESS_LOG", default=False)
    logger.info(
        f"Arrancando FastAPI (uvicorn) en {uvicorn_host}:{uvicorn_port} "
        f"(background, access_log={'ON' if uvicorn_access_log else 'OFF'})..."
    )
    try:
        server, server_task = await start_uvicorn_in_background(
            server_module.app,
            host=uvicorn_host,
            port=uvicorn_port,
            access_log=uvicorn_access_log,
        )
    except RuntimeError as ex:
        logger.error(str(ex))
        raise SystemExit(2)

    auto_register_webhooks = _env_bool("SMS_GATE_AUTO_REGISTER_WEBHOOKS", default=False)
    unregister_on_exit = _env_bool("SMS_GATE_UNREGISTER_ON_EXIT", default=False)
    cloud_api_url = os.getenv("SMS_GATE_API_URL", "https://api.sms-gate.app/3rdparty/v1").strip()
    cloud_api_username = os.getenv("SMS_GATE_API_USERNAME", "").strip()
    cloud_api_password = os.getenv("SMS_GATE_API_PASSWORD", "")
    webhook_url = os.getenv("SMS_GATE_WEBHOOK_URL", "").strip()
    webhook_events = _env_events(
        "SMS_GATE_WEBHOOK_EVENTS",
        "sms:received,sms:sent,sms:delivered,sms:failed",
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
                    "(no usar login de /webhook/sms/device)."
                )

    local_api_mode = _env_bool("SMS_GATE_LOCAL_API_ENABLED", default=False)
    if local_api_mode:
        logger.info("SMS_GATE_LOCAL_API_ENABLED=1 -> omitiendo espera de primer llamado (/device|/message polling).")
        logger.info(
            "Local API base URL activa: {}",
            os.getenv("SMS_GATE_LOCAL_API_BASE_URL", "http://127.0.0.1:18080"),
        )
    else:
        if Path("/.dockerenv").exists():
            logger.warning(
                "Contenedor en modo polling/espera. "
                "Define SMS_GATE_LOCAL_API_ENABLED=1 para flujo ADB local."
            )
        timeout_seconds = 300
        try:
            if hasattr(server_module, "first_request_event"):
                if timeout_seconds and timeout_seconds > 0:
                    logger.info(f"Esperando primer llamado de la app (timeout={timeout_seconds}s)...")
                    await asyncio.wait_for(server_module.first_request_event.wait(), timeout=timeout_seconds)
                else:
                    logger.info("Esperando primer llamado de la app (sin timeout)...")
                    await server_module.first_request_event.wait()
                logger.info("Primer llamado recibido: arrancando scheduler/procesamiento.")
            else:
                logger.warning("server_module.first_request_event no existe - procediendo sin espera.")
        except asyncio.TimeoutError:
            logger.warning(f"No se recibió primer llamado en {timeout_seconds}s - procediendo según configuración.")
        except Exception as ex:
            logger.exception(f"Error esperando primer llamado de la app: {ex}")

    excel_paths = _parse_excel_paths(os.getenv("EXCEL_PATH", ""))
    if not excel_paths:
        raise ValueError("EXCEL_PATH no está definido. Puedes colocar uno o varios archivos separados por ';' o ','.")

    sms_service = SMSService(
        retries=_env_int("SMS_GATE_SMS_RETRIES", 1, min_value=1, max_value=10),
        delay=_env_int("SMS_GATE_SMS_RETRY_DELAY_SECONDS", 30, min_value=0, max_value=3600),
        timeout=_env_int("SMS_GATE_SMS_TIMEOUT_SECONDS", 30, min_value=1, max_value=3600),
    )

    schedule_enabled = _env_bool("SMS_GATE_SCHEDULE_ENABLED", default=True)
    run_times = _parse_daily_run_times(os.getenv("SMS_GATE_DAILY_RUN_TIMES", "08:00,14:00,20:00"))
    skip_past_rounds = _env_bool("SMS_GATE_SKIP_PAST_ROUNDS", default=True)
    offline_alert_recipients = _env_list("SMS_GATE_OFFLINE_ALERT_RECIPIENTS", "04143417356")
    runtime_tz = _resolve_runtime_timezone(logger)
    maintenance_flag_raw = os.getenv("SMS_GATE_MAINTENANCE_FLAG_PATH", "data/maintenance.pause").strip()
    maintenance_flag_path = Path(_normalize_excel_path(maintenance_flag_raw)) if maintenance_flag_raw else None
    maintenance_recheck_seconds = _env_int(
        "SMS_GATE_MAINTENANCE_RECHECK_SECONDS",
        60,
        min_value=5,
        max_value=3600,
    )

    logger.info(
        f"Modo scheduler={'ON' if schedule_enabled else 'OFF'} | "
        f"horas={', '.join(t.strftime('%H:%M:%S') for t in run_times)} | "
        f"skip_pasadas={'1' if skip_past_rounds else '0'}"
    )
    if maintenance_flag_path:
        logger.info(f"Mantenimiento por bandera de archivo: {maintenance_flag_path}")
    logger.info(f"Destinatarios alerta OFFLINE: {offline_alert_recipients or ['(sin configurar)']}")

    try:
        if schedule_enabled:
            await _run_daily_scheduler(
                excel_paths=excel_paths,
                sms_service=sms_service,
                run_times=run_times,
                skip_past_rounds=skip_past_rounds,
                offline_alert_recipients=offline_alert_recipients,
                runtime_tz=runtime_tz,
                maintenance_flag_path=maintenance_flag_path,
                maintenance_recheck_seconds=maintenance_recheck_seconds,
                logger=logger,
            )
        else:
            await _run_single_batch(
                excel_paths=excel_paths,
                sms_service=sms_service,
                offline_alert_recipients=offline_alert_recipients,
                runtime_tz=runtime_tz,
                logger=logger,
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
