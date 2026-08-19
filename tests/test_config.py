import os
from datetime import time

import pytest

from src.core import config


@pytest.fixture
def empty_env(tmp_path, monkeypatch):
    for key in list(os.environ.keys()):
        if key.startswith("SMS_GATE_") or key.startswith("EMAIL_") or key == "EXCEL_PATH":
            monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("")
    return env_file


def test_bool_true_values(empty_env, monkeypatch):
    for value in ("true", "1", "yes", "on", "TRUE", "On"):
        monkeypatch.setenv("SMS_GATE_ACCESS_LOG", value)
        assert config.Settings.load(env_file=empty_env).access_log is True


def test_bool_false_values(empty_env, monkeypatch):
    for value in ("false", "0", "off", "no", "FALSE"):
        monkeypatch.setenv("SMS_GATE_ACCESS_LOG", value)
        assert config.Settings.load(env_file=empty_env).access_log is False


def test_bool_empty_default_false(empty_env, monkeypatch):
    monkeypatch.setenv("SMS_GATE_ACCESS_LOG", "")
    assert config.Settings.load(env_file=empty_env).access_log is False


def test_bool_default_true_when_unset(empty_env):
    assert config.Settings.load(env_file=empty_env).schedule_enabled is True


def test_int_clamping(empty_env, monkeypatch):
    cases = [
        ("SMS_GATE_SERVER_PORT", "server_port", 1, 65535),
        ("SMS_GATE_SMS_RETRIES", "sms_retries", 1, 10),
        ("SMS_GATE_SMS_TIMEOUT_SECONDS", "sms_timeout_seconds", 1, 3600),
        ("SMS_GATE_MAINTENANCE_RECHECK_SECONDS", "maintenance_recheck_seconds", 5, 3600),
        ("EMAIL_SMTP_TIMEOUT_SECONDS", "email_smtp_timeout_seconds", 3, 300),
    ]
    for env_name, attr, low, high in cases:
        monkeypatch.setenv(env_name, str(low - 1))
        assert getattr(config.Settings.load(env_file=empty_env), attr) == low
        monkeypatch.setenv(env_name, str(high + 1))
        assert getattr(config.Settings.load(env_file=empty_env), attr) == high


def test_int_min_only(empty_env, monkeypatch):
    monkeypatch.setenv("SMS_GATE_TIMESTAMP_TOLERANCE_SECONDS", "-5")
    assert config.Settings.load(env_file=empty_env).timestamp_tolerance_seconds == 0
    monkeypatch.setenv("SMS_GATE_MAX_TRACKED_DELIVERIES", "10")
    assert config.Settings.load(env_file=empty_env).max_tracked_deliveries == 100


def test_list_parsing(empty_env, monkeypatch):
    monkeypatch.setenv("SMS_GATE_WEBHOOK_EVENTS", "a, b;c,;d")
    assert config.Settings.load(env_file=empty_env).webhook_events == ["a", "b", "c", "d"]


def test_email_list_validation(empty_env, monkeypatch):
    monkeypatch.setenv(
        "EMAIL_REPORT_RECIPIENTS",
        "a@x.com; b@x.com,not-an-email, c@x.com # comment, A@X.COM",
    )
    assert config.Settings.load(env_file=empty_env).email_report_recipients == [
        "a@x.com",
        "b@x.com",
        "c@x.com",
    ]


def test_daily_run_times(empty_env, monkeypatch):
    monkeypatch.setenv("SMS_GATE_DAILY_RUN_TIMES", "20:00,08:00:30,08:00,20:00")
    assert config.Settings.load(env_file=empty_env).daily_run_times == [
        time(8, 0),
        time(8, 0, 30),
        time(20, 0),
    ]


def test_daily_run_times_fallback_when_empty(empty_env, monkeypatch):
    monkeypatch.setenv("SMS_GATE_DAILY_RUN_TIMES", "")
    assert config.Settings.load(env_file=empty_env).daily_run_times == [
        time(8, 0),
        time(14, 0),
        time(20, 0),
    ]


def test_excel_path_single_file(empty_env, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_text("")
    monkeypatch.setenv("EXCEL_PATH", str(xlsx))
    assert config.Settings.load(env_file=empty_env).excel_paths == [str(xlsx)]


def test_excel_path_directory(empty_env, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_text("")
    (tmp_path / "b.txt").write_text("")
    monkeypatch.setenv("EXCEL_PATH", str(tmp_path))
    assert config.Settings.load(env_file=empty_env).excel_paths == [str(xlsx)]


def test_excel_path_glob(empty_env, tmp_path, monkeypatch):
    xlsx = tmp_path / "a.xlsx"
    xlsx.write_text("")
    (tmp_path / "b.xlsm").write_text("")
    (tmp_path / "c.txt").write_text("")
    monkeypatch.setenv("EXCEL_PATH", str(tmp_path / "*.xls*"))
    assert config.Settings.load(env_file=empty_env).excel_paths == [str(xlsx), str(tmp_path / "b.xlsm")]


def test_normalize_excel_path_windows_drive():
    assert config.normalize_excel_path(r"C:\data\file.xlsx") == "/mnt/c/data/file.xlsx"


def test_inline_env_precedence(empty_env, monkeypatch):
    env_file = empty_env
    env_file.write_text("SMS_GATE_SERVER_PORT=9999\n")
    monkeypatch.setenv("SMS_GATE_SERVER_PORT", "8123")
    assert config.Settings.load(env_file=env_file).server_port == 8123


def test_local_api_country_code_default(empty_env):
    assert config.Settings.load(env_file=empty_env).local_api_country_code == "58"
