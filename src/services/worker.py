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

            # Actualizar estado
            df.at[index, "Estado"] = status

            if status == "ONLINE":
                metrics.success += 1
                logger.success(f"{phone} ONLINE")

            else:
                metrics.inoperative += 1
                df.at[index, "Estado"] = "INOPERATIVO"
                logger.warning(f"{phone} marcado INOPERATIVO")
                
            print("\n")

        except Exception as e:
            metrics.errors += 1
            metrics.inoperative += 1

            df.at[index, "Estado"] = "INOPERATIVO"
            df.at[index, "Error"] = str(e)

            logger.error(f"{phone} error final: {e}")

        finally:
            queue.task_done()
