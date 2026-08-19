import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from pathlib import Path
from typing import Any

from ..core.commands import COMMANDS
from ..core.config import normalize_excel_path
from ..core.validator import validate_devices
from ..storage.excel import load_devices, save_devices
from .email_service import EmailReportService
from .metrics import Metrics
from .persistence import RunPersistence
from .queue_manager import process_devices
from .sms_service import SMSService

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


def _apply_result_to_metrics(metrics: Metrics, status: str, error_code: str) -> None:
    normalized = _normalize_status(status)
    if normalized in ("ONLINE", "UNKNOWN"):
        metrics.success += 1
    else:
        metrics.inoperative += 1
    if error_code in {"WORKER_HARD_TIMEOUT", "UNHANDLED_EXCEPTION"}:
        metrics.errors += 1


def _prepare_daily_excel_states(
    excel_paths: list[str],
    logger,
    persistence: RunPersistence | None = None,
) -> list[DailyExcelState]:
    logger = logger.bind(component="day")
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
        if persistence is not None:
            persisted = persistence.get_aggregate(excel_path)
            if persisted:
                for idx in valid_indexes:
                    key = str(idx)
                    if key in persisted:
                        item = persisted[key]
                        aggregate[idx] = DeviceAggregate(
                            status=str(item.get("status") or "OFFLINE"),
                            error=str(item.get("error") or ""),
                            rounds_observed=int(item.get("rounds_observed") or 0),
                        )
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
    persistence: RunPersistence | None = None,
):
    logger = logger.bind(component="day")
    logger.info(f"========== RONDA {round_number}/{total_rounds} ==========")
    round_index = max(round_number - 1, 0)
    if persistence is not None:
        persistence.mark_round_started(round_index)

    round_completed = False
    try:
        for state in day_states:
            round_df = state.base_df.copy(deep=True)
            round_df["Status"] = ""
            if "Error" not in round_df.columns:
                round_df["Error"] = ""
            else:
                round_df["Error"] = ""

            round_metrics = Metrics()
            round_results = {}
            if persistence is not None:
                round_results = persistence.get_round_results(round_index, state.path)

            invalid_index_map = {str(idx): idx for idx, _ in state.invalid_devices}
            valid_index_map = {str(idx): idx for idx in state.valid_indexes}
            if round_results:
                skipped_valid = sum(1 for key in round_results if key in valid_index_map)
                skipped_invalid = sum(1 for key in round_results if key in invalid_index_map)
                if skipped_valid or skipped_invalid:
                    logger.info(
                        f"Reanudando {state.path}: "
                        f"saltados {skipped_valid + skipped_invalid} dispositivos ya procesados "
                        f"(validos={skipped_valid} no_soportados={skipped_invalid})"
                    )

            if round_results:
                for idx_key, payload in round_results.items():
                    idx = valid_index_map.get(idx_key)
                    if idx is None:
                        idx = invalid_index_map.get(idx_key)
                    if idx is None:
                        continue
                    status_value = str(payload.get("status") or "").strip().upper()
                    error_value = str(payload.get("error") or "").strip()
                    round_df.at[idx, "Status"] = status_value
                    if "Error" in round_df.columns:
                        round_df.at[idx, "Error"] = error_value
                    if idx_key in invalid_index_map:
                        round_metrics.unsupported += 1
                    else:
                        _apply_result_to_metrics(round_metrics, status_value, error_value)

            for idx, error_message in state.invalid_devices:
                idx_key = str(idx)
                if idx_key in round_results:
                    continue
                round_metrics.unsupported += 1
                round_df.at[idx, "Status"] = "UNKNOWN"
                round_df.at[idx, "Error"] = error_message
                logger.warning(f"Fila no soportada ({state.path}): {error_message}")
                if persistence is not None:
                    persistence.record_round_result(round_index, state.path, idx, "UNKNOWN", error_message)

            valid_pending = [idx for idx in state.valid_indexes if str(idx) not in round_results]
            if valid_pending:
                def _record_result(row_index, status, error_code, _outcome):
                    if persistence is None:
                        return
                    persistence.record_round_result(round_index, state.path, row_index, status, error_code)

                await process_devices(
                    df=round_df,
                    valid_indexes=valid_pending,
                    sms_service=sms_service,
                    metrics=round_metrics,
                    max_concurrent_sms=MAX_CONCURRENT_SMS,
                    num_workers=NUM_WORKERS,
                    result_callback=_record_result,
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

            if persistence is not None:
                persistence.save_aggregate(state.path, state.aggregate)
        round_completed = True
    finally:
        if round_completed and persistence is not None:
            persistence.mark_round_completed(round_index)


def _build_offline_alert_messages(day_label: str, offline_devices: list[dict[str, str]], max_chars: int = 150) -> list[str]:
    if not offline_devices:
        return []

    def _clip(value: str, max_len: int) -> str:
        text = str(value or "").strip()
        if len(text) <= max_len:
            return text
        if max_len <= 3:
            return text[:max_len]
        return text[: max_len - 3] + "..."

    def _one_device_message(item: dict[str, str]) -> str:
        doc_name = Path(str(item.get("excel_path", "") or "")).name or "SIN_DOCUMENTO"
        sheet_name = str(item.get("sheet", "")).strip() or "SIN_HOJA"
        phone = str(item.get("phone", "")).strip() or "SIN_NUMERO"
        brand = str(item.get("brand", "")).strip().upper() or "SIN_MARCA"
        model = str(item.get("model", "")).strip().upper() or "SIN_MODELO"
        plate = str(item.get("plate", "")).strip().upper() or "SIN_PLACA"

        message = (
            f"OFFLINE {day_label}\n"
            f"Doc:{doc_name} Hoja:{sheet_name}\n"
            f"Placa:{plate}\n"
            f"Eq:{brand}/{model}\n"
            f"Tel:{phone}"
        )
        if len(message) <= max_chars:
            return message

        doc_name = _clip(doc_name, 18) or "SIN_DOC"
        sheet_name = _clip(sheet_name, 12) or "SIN_HOJA"
        plate = _clip(plate, 10) or "SIN_PLACA"
        brand = _clip(brand, 12) or "SIN_MARCA"
        model = _clip(model, 12) or "SIN_MODELO"

        compact = (
            f"OFFLINE {day_label}\n"
            f"{doc_name}|{sheet_name}\n"
            f"Pl:{plate} Eq:{brand}/{model}\n"
            f"Tel:{phone}"
        )
        if len(compact) <= max_chars:
            return compact

        single_line = f"OFFLINE {day_label} Tel:{phone} Pl:{plate} Eq:{brand}/{model} Doc:{doc_name}|{sheet_name}"
        if len(single_line) <= max_chars:
            return single_line
        return single_line[:max_chars]

    ordered = sorted(
        offline_devices,
        key=lambda it: (
            str(it.get("excel_path", "")),
            str(it.get("sheet", "")),
            str(it.get("plate", "")),
            str(it.get("phone", "")),
        ),
    )
    return [_one_device_message(item) for item in ordered]


async def _notify_offline_devices(
    day_label: str,
    offline_devices: list[dict[str, str]],
    sms_service: SMSService,
    recipients: list[str],
    logger,
):
    logger = logger.bind(component="day")
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


def _collect_excel_attachments(day_states: list[DailyExcelState]) -> list[str]:
    attachments: list[str] = []
    seen: set[str] = set()
    for state in day_states:
        normalized = normalize_excel_path(state.path)
        if not normalized or normalized in seen:
            continue
        path = Path(normalized)
        if not path.is_file():
            continue
        seen.add(normalized)
        attachments.append(normalized)
    return attachments


async def _notify_email_report(
    day_label: str,
    day_states: list[DailyExcelState],
    offline_count: int,
    email_service: EmailReportService | None,
    recipients: list[str],
    subject_prefix: str,
    logger,
):
    logger = logger.bind(component="day")
    if email_service is None:
        logger.info("Reporte por correo desactivado o sin configuración SMTP. No se enviará email.")
        return
    if not recipients:
        logger.info("EMAIL_REPORT_RECIPIENTS vacío. No se enviará reporte por correo.")
        return

    attachments = _collect_excel_attachments(day_states)
    if not attachments:
        logger.warning("No hay archivos Excel para adjuntar en el reporte por correo.")
        return

    subject_head = subject_prefix or "Ceproalarm SMS Gateway"
    subject = f"[{subject_head}] Reporte diario de localizadores - {day_label}"
    body = (
        f"Estimado equipo,\n\n"
        f"Se adjuntan los archivos Excel procesados en la jornada {day_label}.\n\n"
        f"Resumen:\n"
        f"- Archivos adjuntos: {len(attachments)}\n"
        f"- Localizadores OFFLINE al cierre: {offline_count}\n\n"
        f"Atentamente,\n"
        f"{subject_head}"
    )

    try:
        result = await email_service.send_report(
            recipients=recipients,
            subject=subject,
            body=body,
            attachment_paths=attachments,
        )
        logger.info(
            "Reporte por correo enviado: destinatarios={} adjuntos={} asunto='{}'".format(
                result.get("sent_to", 0),
                result.get("attachments", 0),
                result.get("subject", subject),
            )
        )
    except Exception as ex:
        logger.error(f"No se pudo enviar reporte por correo: {ex}")


async def _finalize_day(
    day_date: date,
    day_states: list[DailyExcelState],
    sms_service: SMSService,
    offline_alert_recipients: list[str],
    email_service: EmailReportService | None,
    email_report_recipients: list[str],
    email_subject_prefix: str,
    logger,
    persistence: RunPersistence | None = None,
):
    logger = logger.bind(component="day")
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
                        "plate": str(row.get("Placas", "")).strip(),
                        "sheet": str(row.get("__sheet", "")).strip(),
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
        key = (
            item.get("excel_path", ""),
            item.get("sheet", ""),
            item.get("phone", ""),
            item.get("plate", ""),
        )
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
    await _notify_email_report(
        day_label=day_label,
        day_states=day_states,
        offline_count=len(deduped_offline),
        email_service=email_service,
        recipients=email_report_recipients,
        subject_prefix=email_subject_prefix,
        logger=logger,
    )
    logger.info(
        f"Cierre de jornada {day_label} completado. "
        f"Total OFFLINE finales={len(deduped_offline)}"
    )
    if persistence is not None:
        persistence.clear()


async def _run_single_batch(
    excel_paths: list[str],
    sms_service: SMSService,
    offline_alert_recipients: list[str],
    email_service: EmailReportService | None,
    email_report_recipients: list[str],
    email_subject_prefix: str,
    runtime_tz: tzinfo,
    logger,
    persistence: RunPersistence | None = None,
):
    logger = logger.bind(component="day")
    logger.info("SMS_GATE_SCHEDULE_ENABLED=0 -> ejecución única")
    day_states = _prepare_daily_excel_states(excel_paths, logger, persistence=persistence)
    if not day_states:
        logger.warning("No hay Excel válidos para procesar en ejecución única.")
        return
    await _execute_round_for_day(
        day_states=day_states,
        sms_service=sms_service,
        round_number=1,
        total_rounds=1,
        logger=logger,
        persistence=persistence,
    )
    await _finalize_day(
        day_date=datetime.now(runtime_tz).date(),
        day_states=day_states,
        sms_service=sms_service,
        offline_alert_recipients=offline_alert_recipients,
        email_service=email_service,
        email_report_recipients=email_report_recipients,
        email_subject_prefix=email_subject_prefix,
        logger=logger,
        persistence=persistence,
    )