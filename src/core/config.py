"""Centralized configuration.

Every environment variable is read once here into a single Settings
instance. Modules consume the shared singleton instead of calling
os.getenv themselves, so parsing rules and defaults live in one place.
"""

import glob
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parents[2]

SUPPORTED_EXCEL_EXTENSIONS = {".xlsx", ".xlsm", ".xltx", ".xltm"}


def _parse_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "1" if default else "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(name: str, default: int, min_value: int = 0, max_value: int | None = None) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except Exception:
        value = default
    if value < min_value:
        return min_value
    if max_value is not None and value > max_value:
        return max_value
    return value


def _parse_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _parse_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
    return [p for p in parts if p]


def _parse_email_list(name: str, default: str = "") -> list[str]:
    email_pattern = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    clean: list[str] = []
    seen: set[str] = set()
    for value in _parse_list(name, default):
        candidate = str(value).strip()
        if "#" in candidate:
            candidate = candidate.split("#", 1)[0].strip()
        if not candidate:
            continue
        if not email_pattern.match(candidate):
            continue
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        clean.append(candidate)
    return clean


def normalize_excel_path(value: str) -> str:
    """Normalize a user-supplied path: strip quotes, expand ~/vars, and on
    POSIX convert Windows drive letters (C:\...) to /mnt/c/..."""
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

    resolved: list[str] = []
    seen: set[str] = set()

    for part in parts:
        normalized = normalize_excel_path(part)
        has_glob = any(ch in normalized for ch in ("*", "?", "["))
        candidates: list[str] = []

        if has_glob:
            candidates = sorted(glob.glob(normalized, recursive=True))
        else:
            path_obj = Path(normalized)
            if path_obj.is_dir():
                candidates = sorted(str(p) for p in path_obj.iterdir() if p.is_file())
            else:
                candidates = [normalized]

        for candidate in candidates:
            normalized_candidate = normalize_excel_path(candidate)
            candidate_path = Path(normalized_candidate)
            if not candidate_path.is_file():
                continue
            if candidate_path.suffix.lower() not in SUPPORTED_EXCEL_EXTENSIONS:
                continue
            if normalized_candidate in seen:
                continue
            seen.add(normalized_candidate)
            resolved.append(normalized_candidate)

    return resolved


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


@dataclass
class Settings:
    """All environment-driven configuration for the gateway."""

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    access_log: bool = False

    # Cloud webhook registration (optional)
    auto_register_webhooks: bool = False
    unregister_on_exit: bool = False
    api_url: str = "https://api.sms-gate.app/3rdparty/v1"
    api_username: str = ""
    api_password: str = ""
    webhook_url: str = ""
    webhook_events: list[str] = field(
        default_factory=lambda: ["sms:received", "sms:sent", "sms:delivered", "sms:failed"]
    )
    device_id: str | None = None

    # Webhook signature verification
    webhook_signing_key: str = ""
    require_signature: bool = False
    timestamp_tolerance_seconds: int = 300
    max_tracked_deliveries: int = 5000

    # Local API (SMS Gateway Android app, ADB mode)
    local_api_enabled: bool = False
    local_api_base_url: str = "http://127.0.0.1:18080"
    local_api_username: str = "sms"
    local_api_password: str = ""
    local_api_country_code: str = "58"

    # Excel processing
    excel_paths: list[str] = field(default_factory=list)

    # SMS runtime behavior
    sms_retries: int = 1
    sms_retry_delay_seconds: int = 30
    sms_timeout_seconds: int = 30

    # Scheduler
    schedule_enabled: bool = True
    daily_run_times: list[dt_time] = field(default_factory=list)
    skip_past_rounds: bool = True
    skip_grace_seconds: int = 60
    timezone: str = ""
    maintenance_flag_path: str = "data/maintenance.pause"
    maintenance_recheck_seconds: int = 60
    persistence_enabled: bool = True
    persistence_path: str = "data/run_state.json"

    # End-of-day offline alerts
    offline_alert_recipients: list[str] = field(default_factory=lambda: ["04143417356"])

    # End-of-day email report
    email_report_enabled: bool = False
    email_report_recipients: list[str] = field(default_factory=list)
    email_report_subject_prefix: str = "Ceproalarm SMS Gateway"
    email_smtp_host: str = ""
    email_smtp_port: int = 587
    email_smtp_username: str = ""
    email_smtp_password: str = ""
    email_from: str = ""
    email_smtp_use_ssl: bool = False
    email_smtp_use_tls: bool = True
    email_smtp_timeout_seconds: int = 20

    # Logging
    log_path: str = ""

    @classmethod
    def load(cls, env_file: str | os.PathLike | None = None) -> "Settings":
        # Inline env vars already in os.environ win over .env (override=False).
        load_dotenv(env_file if env_file is not None else _REPO_ROOT / ".env", override=False)

        smtp_username = _parse_str("EMAIL_SMTP_USERNAME", "")
        smtp_use_ssl = _parse_bool("EMAIL_SMTP_USE_SSL", default=False)
        smtp_use_tls = _parse_bool("EMAIL_SMTP_USE_TLS", default=not smtp_use_ssl)
        if smtp_use_ssl:
            smtp_use_tls = False

        return cls(
            server_host=_parse_str("SMS_GATE_SERVER_HOST", "0.0.0.0") or "0.0.0.0",
            server_port=_parse_int("SMS_GATE_SERVER_PORT", 8000, min_value=1, max_value=65535),
            access_log=_parse_bool("SMS_GATE_ACCESS_LOG", default=False),
            auto_register_webhooks=_parse_bool("SMS_GATE_AUTO_REGISTER_WEBHOOKS", default=False),
            unregister_on_exit=_parse_bool("SMS_GATE_UNREGISTER_ON_EXIT", default=False),
            api_url=_parse_str("SMS_GATE_API_URL", "https://api.sms-gate.app/3rdparty/v1"),
            api_username=_parse_str("SMS_GATE_API_USERNAME", ""),
            api_password=os.getenv("SMS_GATE_API_PASSWORD", ""),
            webhook_url=_parse_str("SMS_GATE_WEBHOOK_URL", ""),
            webhook_events=_parse_list(
                "SMS_GATE_WEBHOOK_EVENTS", "sms:received,sms:sent,sms:delivered,sms:failed"
            ),
            device_id=_parse_str("SMS_GATE_DEVICE_ID", "") or None,
            webhook_signing_key=_parse_str("SMS_GATE_WEBHOOK_SIGNING_KEY", ""),
            require_signature=_parse_bool("SMS_GATE_REQUIRE_SIGNATURE", default=False),
            timestamp_tolerance_seconds=_parse_int(
                "SMS_GATE_TIMESTAMP_TOLERANCE_SECONDS", 300, min_value=0
            ),
            max_tracked_deliveries=_parse_int("SMS_GATE_MAX_TRACKED_DELIVERIES", 5000, min_value=100),
            local_api_enabled=_parse_bool("SMS_GATE_LOCAL_API_ENABLED", default=False),
            local_api_base_url=_parse_str(
                "SMS_GATE_LOCAL_API_BASE_URL", "http://127.0.0.1:18080"
            ).rstrip("/"),
            local_api_username=_parse_str("SMS_GATE_LOCAL_API_USERNAME", "sms"),
            local_api_password=os.getenv("SMS_GATE_LOCAL_API_PASSWORD", ""),
            local_api_country_code=_parse_str("SMS_GATE_LOCAL_API_COUNTRY_CODE", "58"),
            excel_paths=_parse_excel_paths(os.getenv("EXCEL_PATH", "")),
            sms_retries=_parse_int("SMS_GATE_SMS_RETRIES", 1, min_value=1, max_value=10),
            sms_retry_delay_seconds=_parse_int(
                "SMS_GATE_SMS_RETRY_DELAY_SECONDS", 30, min_value=0, max_value=3600
            ),
            sms_timeout_seconds=_parse_int("SMS_GATE_SMS_TIMEOUT_SECONDS", 30, min_value=1, max_value=3600),
            schedule_enabled=_parse_bool("SMS_GATE_SCHEDULE_ENABLED", default=True),
            daily_run_times=_parse_daily_run_times(
                os.getenv("SMS_GATE_DAILY_RUN_TIMES", "08:00,14:00,20:00")
            ),
            skip_past_rounds=_parse_bool("SMS_GATE_SKIP_PAST_ROUNDS", default=True),
            skip_grace_seconds=_parse_int("SMS_GATE_SKIP_GRACE_SECONDS", 60, min_value=0, max_value=3600),
            timezone=_parse_str("SMS_GATE_TIMEZONE", ""),
            maintenance_flag_path=normalize_excel_path(
                _parse_str("SMS_GATE_MAINTENANCE_FLAG_PATH", "data/maintenance.pause")
            ),
            maintenance_recheck_seconds=_parse_int(
                "SMS_GATE_MAINTENANCE_RECHECK_SECONDS", 60, min_value=5, max_value=3600
            ),
            persistence_enabled=_parse_bool("SMS_GATE_PERSISTENCE_ENABLED", default=True),
            persistence_path=normalize_excel_path(
                _parse_str("SMS_GATE_PERSISTENCE_PATH", "data/run_state.json")
            ),
            offline_alert_recipients=_parse_list("SMS_GATE_OFFLINE_ALERT_RECIPIENTS", "04143417356"),
            email_report_enabled=_parse_bool("EMAIL_REPORT_ENABLED", default=False),
            email_report_recipients=_parse_email_list("EMAIL_REPORT_RECIPIENTS", ""),
            email_report_subject_prefix=(
                _parse_str("EMAIL_REPORT_SUBJECT_PREFIX", "Ceproalarm SMS Gateway")
                or "Ceproalarm SMS Gateway"
            ),
            email_smtp_host=_parse_str("EMAIL_SMTP_HOST", ""),
            email_smtp_port=_parse_int("EMAIL_SMTP_PORT", 587, min_value=1, max_value=65535),
            email_smtp_username=smtp_username,
            email_smtp_password=os.getenv("EMAIL_SMTP_PASSWORD", ""),
            email_from=_parse_str("EMAIL_FROM", "") or smtp_username,
            email_smtp_use_ssl=smtp_use_ssl,
            email_smtp_use_tls=smtp_use_tls,
            email_smtp_timeout_seconds=_parse_int(
                "EMAIL_SMTP_TIMEOUT_SECONDS", 20, min_value=3, max_value=300
            ),
            log_path=_parse_str("SMS_GATE_LOG_PATH", ""),
        )


settings = Settings.load()