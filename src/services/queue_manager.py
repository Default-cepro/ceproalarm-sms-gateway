import asyncio
from loguru import logger
from .worker import Worker
from ..core.commands import get_command


async def process_devices(
    df,
    valid_indexes,
    sms_service,
    metrics,
    max_concurrent_sms: int = 1,
    num_workers: int = 3
):
    queue = asyncio.Queue()

    # Control de concurrencia (1 si hay un solo modem físico)
    semaphore = asyncio.Semaphore(max_concurrent_sms)

    logger.info(f"Encolando {len(valid_indexes)} dispositivos válidos")

    # Cargar dispositivos en la cola
    for index in valid_indexes:
        row = df.loc[index]

        brand = str(row["Marca"])
        model = str(row["Modelo"])

        try:
            command_data = get_command(brand, model)

            # Guardamos command_data dentro del row
            row = row.copy()
            row["command_data"] = command_data

            await queue.put((index, row))

        except Exception as e:
            metrics.unsupported += 1
            df.at[index, "Estado"] = "NO SOPORTADO"
            df.at[index, "Error"] = str(e)
            logger.warning(f"{brand} {model} no soportado: {e}")

    logger.info("Iniciando workers...")

    workers = [
        asyncio.create_task(
            Worker(
                name=f"W{i+1}",
                queue=queue,
                df=df,
                metrics=metrics,
                semaphore=semaphore,
                sms_service=sms_service
            )
        )
        for i in range(num_workers)
    ]

    # Esperar a que la cola termine
    await queue.join()

    logger.info("Todos los dispositivos fueron procesados")

    # Cancelar workers infinitos
    for w in workers:
        w.cancel()

    await asyncio.gather(*workers, return_exceptions=True)
