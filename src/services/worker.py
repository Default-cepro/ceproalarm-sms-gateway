import asyncio
from loguru import logger
from ..api.server import normalize_phone


async def Worker(
    name: str,
    queue: asyncio.Queue,
    df,
    metrics,
    semaphore,
    sms_service,
    progress_callback=None,
    result_callback=None,
):
    logger.info(f"Worker {name} iniciado")
    while True:
        index, row = await queue.get()

        phone_raw = row.get("Telefono", "")
        phone = normalize_phone(str(phone_raw))

        def _emit_progress():
            if progress_callback is None:
                return
            try:
                progress_callback()
            except Exception:
                pass

        def _emit_result(status: str, error_code: str, outcome: str):
            if result_callback is None:
                return
            try:
                result_callback(index, status, error_code, outcome)
            except Exception:
                pass

        try:
            if not phone:
                metrics.errors += 1
                metrics.inoperative += 1
                df.at[index, "Status"] = "OFFLINE"
                if "Error" in df.columns:
                    df.at[index, "Error"] = "INVALID_PHONE"
                logger.error(f"Fila con teléfono inválido: {phone_raw!r}")
                _emit_result("OFFLINE", "INVALID_PHONE", "error")
                _emit_progress()
                continue

            async with semaphore:
                logger.debug(f"[{name}] Procesando {phone}")

                command_data = row["command_data"]
                message = command_data["command"]
                expected = command_data["expected"]

                retries = max(int(getattr(sms_service, "retries", 1) or 1), 1)
                delay = max(int(getattr(sms_service, "delay", 0) or 0), 0)
                timeout = max(int(getattr(sms_service, "timeout", 30) or 30), 1)
                hard_timeout_seconds = (timeout * retries) + (delay * max(retries - 1, 0)) + 5

                status = await asyncio.wait_for(
                    sms_service.send_with_retry(phone, message, expected),
                    timeout=hard_timeout_seconds,
                )

            final_status = status.get("status", "OFFLINE")
            error_code = status.get("error_code", "")
            df.at[index, "Status"] = final_status
            if "Error" in df.columns:
                df.at[index, "Error"] = error_code

            if final_status in ("ONLINE", "UNKNOWN"):
                metrics.success += 1
                logger.success(f"{phone} {final_status}")
                _emit_result(final_status, error_code, "success")
            else:
                metrics.inoperative += 1
                logger.warning(f"{phone} marcado OFFLINE (error={error_code or 'N/A'})")
                _emit_result("OFFLINE", error_code or "", "offline")
            _emit_progress()

        except asyncio.TimeoutError:
            metrics.errors += 1
            metrics.inoperative += 1

            df.at[index, "Status"] = "OFFLINE"
            if "Error" in df.columns:
                df.at[index, "Error"] = "WORKER_HARD_TIMEOUT"

            logger.error(f"{phone} timeout duro en worker (pasando al siguiente)")
            _emit_result("OFFLINE", "WORKER_HARD_TIMEOUT", "error")
            _emit_progress()

        except Exception as e:
            metrics.errors += 1
            metrics.inoperative += 1

            df.at[index, "Status"] = "OFFLINE"
            if "Error" in df.columns:
                df.at[index, "Error"] = "UNHANDLED_EXCEPTION"

            logger.error(f"{phone} error final: {e}")
            _emit_result("OFFLINE", "UNHANDLED_EXCEPTION", "error")
            _emit_progress()

        finally:
            queue.task_done()
