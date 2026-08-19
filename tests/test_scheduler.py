from datetime import datetime, timedelta, timezone

from src.services import scheduler


def _run_times():
    return [
        datetime.strptime("08:00", "%H:%M").time(),
        datetime.strptime("14:00", "%H:%M").time(),
        datetime.strptime("20:00", "%H:%M").time(),
    ]


def _day_at(hour, minute=0, tz=timezone.utc):
    return datetime(2026, 8, 19, hour, minute, tzinfo=tz)


def test_skipped_rounds_before_all_rounds():
    now = _day_at(7, 59)
    assert scheduler.skipped_rounds(now, _run_times(), 60) == 0


def test_skipped_rounds_after_round1_within_grace():
    now = _day_at(8, 0, tz=timezone.utc) + timedelta(seconds=30)
    assert scheduler.skipped_rounds(now, _run_times(), 60) == 0


def test_skipped_rounds_after_round1_beyond_grace():
    now = _day_at(8, 0, tz=timezone.utc) + timedelta(seconds=61)
    assert scheduler.skipped_rounds(now, _run_times(), 60) == 1


def test_skipped_rounds_after_all_rounds():
    now = _day_at(20, 0, tz=timezone.utc) + timedelta(seconds=61)
    assert scheduler.skipped_rounds(now, _run_times(), 60) == 3


def test_skipped_rounds_timezone_aware():
    # Same wall-clock time in a different tz must not change the count.
    tz = timezone(timedelta(hours=-4))
    now = datetime(2026, 8, 19, 8, 0, 30, tzinfo=tz)
    assert scheduler.skipped_rounds(now, _run_times(), 60) == 0
    now = datetime(2026, 8, 19, 8, 1, 1, tzinfo=tz)
    assert scheduler.skipped_rounds(now, _run_times(), 60) == 1


async def test_sleep_until_past_target_returns_immediately():
    past = datetime.now(timezone.utc) - timedelta(seconds=60)
    await scheduler._sleep_until(past)
