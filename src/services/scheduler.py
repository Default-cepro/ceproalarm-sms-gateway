import asyncio
from datetime import date, datetime, timedelta, time as dt_time, tzinfo
from pathlib import Path

from .day_lifecycle import (
    DailyExcelState,
    _execute_round_for_day,
    _finalize_day,
    _prepare_daily_excel_states,
)
from .persistence import RunPersistence


def skipped_rounds(now: datetime, run_times: list[dt_time], grace_seconds: int) -> int:
    """Number of today's rounds already past (beyond the grace window) at `now`."""
    return sum(
        1
        for run_time in run_times
        if datetime.combine(now.date(), run_time, tzinfo=now.tzinfo)
        + timedelta(seconds=grace_seconds)
        < now
    )


async def _sleep_until(target_dt: datetime):
    while True:
        now = datetime.now(target_dt.tzinfo)
        remaining = (target_dt - now).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, 30))


async def _run_daily_scheduler(
    excel_paths: list[str],
    sms_service,
    run_times: list[dt_time],
    skip_past_rounds: bool,
    skip_grace_seconds: int,
    offline_alert_recipients: list[str],
    email_service,
    email_report_recipients: list[str],
    email_subject_prefix: str,
    runtime_tz: tzinfo,
    maintenance_flag_path: Path | None,
    maintenance_recheck_seconds: int,
    logger,
    persistence: RunPersistence | None = None,
):
    logger = logger.bind(component="scheduler")
    current_day: date | None = None
    day_states: list[DailyExcelState] = []
    next_round_index = 0
    day_finalized = False
    ran_any_round = False
    resume_round_index: int | None = None
    resume_pending = False

    while True:
        now = datetime.now(runtime_tz)
        if current_day != now.date():
            current_day = now.date()
            if persistence is not None:
                run_time_strings = [t.strftime("%H:%M:%S") for t in run_times]
                persistence.ensure_day(current_day.isoformat(), run_time_strings, excel_paths)
                resume_round_index = persistence.get_in_progress_round_index()
            else:
                resume_round_index = None
            resume_pending = resume_round_index is not None

            day_states = _prepare_daily_excel_states(excel_paths, logger, persistence=persistence)
            next_round_index = 0
            day_finalized = False
            ran_any_round = False

            if resume_round_index is not None:
                next_round_index = resume_round_index
                logger.warning(
                    f"Reanudando ronda {resume_round_index + 1}/{len(run_times)} "
                    f"pendiente por apagado anterior."
                )
            else:
                persisted_next = None
                if persistence is not None:
                    persisted_next = persistence.first_incomplete_round_index()
                if persisted_next is not None:
                    next_round_index = persisted_next

                if skip_past_rounds:
                    skipped = skipped_rounds(now, run_times, skip_grace_seconds)
                    if skipped > next_round_index:
                        next_round_index = skipped
                    if skipped > 0:
                        logger.warning(
                            f"Se omiten {skipped} ronda(s) ya vencidas de hoy "
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
                    email_service=email_service,
                    email_report_recipients=email_report_recipients,
                    email_subject_prefix=email_subject_prefix,
                    logger=logger,
                    persistence=persistence,
                )
                day_finalized = True

            next_day = active_day + timedelta(days=1)
            next_target = datetime.combine(next_day, run_times[0], tzinfo=runtime_tz)
            logger.info(f"Esperando próxima jornada: {next_target.isoformat()}")
            await _sleep_until(next_target)
            continue

        target_dt = datetime.combine(active_day, run_times[next_round_index], tzinfo=runtime_tz)
        now = datetime.now(runtime_tz)
        if not (resume_pending and resume_round_index == next_round_index):
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
                persistence=persistence,
            )
            ran_any_round = True
            if resume_pending and resume_round_index == next_round_index:
                resume_pending = False

        next_round_index += 1