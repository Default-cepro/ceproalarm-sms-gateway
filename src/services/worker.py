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
):
    logger.info(f"Worker {name} iniciado")
    print("\n")

    while True:
        index, row = await queue.get()

        phone_raw = row.get("Telefono", "")
        phone = normalize_phone(str(phone_raw))

        try:
            if not phone:
                metrics.errors += 1
                metrics.inoperative += 1
                df.at[index, "Status"] = "OFFLINE"
                if "Error" in df.columns:
                    df.at[index, "Error"] = "INVALID_PHONE"
                logger.error(f"Fila con teléfono inválido: {phone_raw!r}")
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
            else:
                metrics.inoperative += 1
                logger.warning(f"{phone} marcado OFFLINE (error={error_code or 'N/A'})")

            print("\n")

        except asyncio.TimeoutError:
            metrics.errors += 1
            metrics.inoperative += 1

            df.at[index, "Status"] = "OFFLINE"
            if "Error" in df.columns:
                df.at[index, "Error"] = "WORKER_HARD_TIMEOUT"

            logger.error(f"{phone} timeout duro en worker (pasando al siguiente)")

        except Exception as e:
            metrics.errors += 1
            metrics.inoperative += 1

            df.at[index, "Status"] = "OFFLINE"
            if "Error" in df.columns:
                df.at[index, "Error"] = "UNHANDLED_EXCEPTION"

            logger.error(f"{phone} error final: {e}")

        finally:
            queue.task_done()
