from loguru import logger
from pathlib import Path
import sys

LOGS_PATH = Path(__file__).resolve().parents[2] / "logs" / "app.log"

def setup_logger():
    logger.remove()

    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level}</level> | "
               "{message}"
    )

    logger.add(
        LOGS_PATH,
        rotation="1 MB",
        level="DEBUG",
        format="{time} | {level} | {message}"
    )

    return logger
