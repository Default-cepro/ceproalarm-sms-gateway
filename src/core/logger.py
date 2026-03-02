from loguru import logger
from pathlib import Path
import os
import sys


def _default_logs_path() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "app.log"


def _resolve_logs_path() -> Path | None:
    raw = os.getenv("SMS_GATE_LOG_PATH", "").strip()
    if not raw:
        return _default_logs_path()
    lowered = raw.lower()
    if lowered in {"stdout", "none", "off", "disable", "disabled"}:
        return None
    return Path(os.path.expandvars(os.path.expanduser(raw)))


def setup_logger():
    logger.remove()

    logger.add(
        sys.stdout,
        level="INFO",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
               "<level>{level}</level> | "
               "{message}"
    )

    log_path = _resolve_logs_path()
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            logger.add(
                log_path,
                rotation="1 MB",
                level="DEBUG",
                format="{time} | {level} | {message}"
            )
        except Exception as exc:
            logger.warning(
                "No se pudo escribir log en archivo ({}). Solo stdout. Motivo: {}",
                log_path,
                exc,
            )

    return logger
