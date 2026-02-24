import asyncio
from loguru import logger


async def Worker(
    name: str,
    queue: asyncio.Queue,
    df,
    metrics,
    semaphore,
    sms_service
):
    logger.info(f"Worker {name} iniciado")
    print("\n")

    while True:
        index, row = await queue.get()

        phone = str(row["Teléfono"])
        brand = str(row["Marca"])
        model = str(row["Modelo"])

        try:
            async with semaphore:

                logger.debug(f"[{name}] Procesando {phone}")

                command_data = row["command_data"]
                message = command_data["command"]
                expected = command_data["expected"]

                status = await sms_service.send_with_retry(
                    phone,
                    message,
                    expected
                )

            # Update status
            final_status = status.get("status", "inoperativo")
            error_code = status.get("error_code", "")
            df.at[index, "Estado"] = final_status
            df.at[index, "Error"] = error_code

            if final_status in ("operativo", "operativo sin respuesta esperada"):
                metrics.success += 1
                logger.success(f"{phone} {final_status}")

            else:
                metrics.inoperative += 1
                logger.warning(f"{phone} marcado inoperativo (error={error_code or 'N/A'})")
                
            print("\n")

        except Exception as e:
            metrics.errors += 1
            metrics.inoperative += 1

            df.at[index, "Estado"] = "inoperativo"
            df.at[index, "Error"] = "UNHANDLED_EXCEPTION"

            logger.error(f"{phone} error final: {e}")

        finally:
            queue.task_done()
