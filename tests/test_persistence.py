import json

import pytest

from src.services.persistence import (
    RunPersistence,
    first_incomplete_round_index,
    in_progress_round_index,
    new_day_state,
    record_round_result_state,
    save_aggregate_state,
)


class _Logger:
    def __init__(self):
        self.warnings = []
        self.infos = []

    def bind(self, **kwargs):
        return self

    def info(self, *a, **k):
        self.infos.append(a)

    def warning(self, *a, **k):
        self.warnings.append(a)


class _AggregateItem:
    def __init__(self, status="OFFLINE", error="", rounds_observed=0):
        self.status = status
        self.error = error
        self.rounds_observed = rounds_observed


def _v1_state():
    return {
        "version": 1,
        "day": "2026-08-19",
        "run_times": ["08:00:00", "14:00:00"],
        "excel_paths": ["/tmp/lote.xlsx"],
        "rounds": [
            {"index": 0, "completed": False, "completed_at": None},
            {"index": 1, "completed": False, "completed_at": None},
        ],
        "current_round": None,
        "round_results": {},
        "aggregate": {},
        "updated_at": 123,
    }


def _make_persistence(tmp_path, logger=None):
    return RunPersistence(tmp_path / "run_state.json", logger or _Logger())


# --- pure helpers -----------------------------------------------------------


def test_new_day_state_shape():
    state = new_day_state("2026-08-19", ["08:00:00", "14:00:00"], ["/tmp/a.xlsx"])
    assert state["version"] == 2
    assert state["day"] == "2026-08-19"
    assert state["run_times"] == ["08:00:00", "14:00:00"]
    assert state["excel_paths"] == ["/tmp/a.xlsx"]
    assert state["rounds"] == [
        {"index": 0, "completed": False, "completed_at": None},
        {"index": 1, "completed": False, "completed_at": None},
    ]
    assert state["current_round"] is None
    assert state["round_results"] == {}
    assert state["aggregate"] == {}
    assert isinstance(state["updated_at"], int)


def test_record_round_result_state_pure():
    state = new_day_state("2026-08-19", ["08:00:00"], ["/tmp/a.xlsx"])
    updated = record_round_result_state(state, 0, "/tmp/a.xlsx", 12, "ONLINE", "")
    assert updated["round_results"]["0"]["/tmp/a.xlsx"]["12"] == {"status": "ONLINE", "error": ""}
    assert updated is not state
    assert state["round_results"] == {}
    assert state["updated_at"] == updated["updated_at"]


def test_save_aggregate_state_pure():
    state = new_day_state("2026-08-19", ["08:00:00"], ["/tmp/a.xlsx"])
    updated = save_aggregate_state(state, "/tmp/a.xlsx", {12: _AggregateItem("ONLINE", "", 2)})
    assert updated["aggregate"]["/tmp/a.xlsx"]["12"] == {
        "status": "ONLINE",
        "error": "",
        "rounds_observed": 2,
    }
    assert updated is not state
    assert state["aggregate"] == {}
    assert state["updated_at"] == updated["updated_at"]


def test_first_incomplete_round_index_pure():
    state = new_day_state("2026-08-19", ["08:00:00", "14:00:00"], ["/tmp/a.xlsx"])
    assert first_incomplete_round_index(state) == 0
    state["rounds"][0]["completed"] = True
    assert first_incomplete_round_index(state) == 1
    state["rounds"][1]["completed"] = True
    assert first_incomplete_round_index(state) is None


def test_in_progress_round_index_pure():
    state = new_day_state("2026-08-19", ["08:00:00"], ["/tmp/a.xlsx"])
    assert in_progress_round_index(state) is None
    state["current_round"] = {"index": 0, "started_at": 1}
    assert in_progress_round_index(state) == 0
    state["rounds"][0]["completed"] = True
    assert in_progress_round_index(state) is None


# --- schema versioning and validation ---------------------------------------


def test_v1_file_loads_and_next_write_becomes_v2(tmp_path):
    path = tmp_path / "run_state.json"
    path.write_text(json.dumps(_v1_state()), encoding="utf-8")

    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00", "14:00:00"], ["/tmp/lote.xlsx"])

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["day"] == "2026-08-19"
    assert data["run_times"] == ["08:00:00", "14:00:00"]


def test_missing_file_creates_fresh_state_without_warning(tmp_path):
    logger = _Logger()
    p = _make_persistence(tmp_path, logger)
    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/lote.xlsx"])

    assert (tmp_path / "run_state.json").exists()
    assert logger.warnings == []


def test_corrupt_json_warns_and_resets(tmp_path):
    path = tmp_path / "run_state.json"
    path.write_text("{not json", encoding="utf-8")
    logger = _Logger()
    p = _make_persistence(tmp_path, logger)

    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/lote.xlsx"])

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["day"] == "2026-08-19"
    assert any("No se pudo leer persistencia" in str(w) for w in logger.warnings)


@pytest.mark.parametrize(
    "payload",
    [
        {"version": 2, "day": 123, "run_times": [], "rounds": [], "round_results": {}, "aggregate": {}},
        {"version": 2, "day": "2026-08-19", "run_times": "08:00:00", "rounds": [], "round_results": {}, "aggregate": {}},
        {"version": 2, "day": "2026-08-19", "run_times": [], "rounds": "x", "round_results": {}, "aggregate": {}},
        {"version": 2, "day": "2026-08-19", "run_times": [], "rounds": [], "round_results": [], "aggregate": {}},
        {"version": 2, "day": "2026-08-19", "run_times": [], "rounds": [], "round_results": {}, "aggregate": []},
        {"version": 3, "day": "2026-08-19", "run_times": [], "rounds": [], "round_results": {}, "aggregate": {}},
        {"version": "2", "day": "2026-08-19", "run_times": [], "rounds": [], "round_results": {}, "aggregate": {}},
        {"version": 2, "day": "2026-08-19", "run_times": [], "rounds": [{"index": 0}, "x"], "round_results": {}, "aggregate": {}},
    ],
)
def test_invalid_state_warns_and_resets(tmp_path, payload):
    path = tmp_path / "run_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    logger = _Logger()
    p = _make_persistence(tmp_path, logger)

    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/lote.xlsx"])

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2
    assert data["day"] == "2026-08-19"
    assert any("Persistencia inválida" in str(w) for w in logger.warnings)


# --- backup on write --------------------------------------------------------


def test_bak_created_before_replace_and_overwritten(tmp_path):
    path = tmp_path / "run_state.json"
    bak = tmp_path / "run_state.json.bak"
    p = _make_persistence(tmp_path)

    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/lote.xlsx"])
    assert not bak.exists()

    p.mark_round_started(0)
    assert bak.exists()
    bak_data = json.loads(bak.read_text(encoding="utf-8"))
    assert bak_data["current_round"] is None
    assert bak_data["rounds"][0]["completed"] is False

    p.mark_round_completed(0)
    bak_data = json.loads(bak.read_text(encoding="utf-8"))
    assert bak_data["current_round"]["index"] == 0
    assert bak_data["rounds"][0]["completed"] is False


# --- round lifecycle --------------------------------------------------------


def test_round_resume_helpers(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00", "14:00:00"], ["/tmp/lote.xlsx"])

    assert p.get_in_progress_round_index() is None
    assert p.first_incomplete_round_index() == 0

    p.mark_round_started(0)
    assert p.get_in_progress_round_index() == 0

    p.mark_round_completed(0)
    assert p.get_in_progress_round_index() is None
    assert p.first_incomplete_round_index() == 1

    p.mark_round_started(1)
    assert p.get_in_progress_round_index() == 1


def test_first_incomplete_round_with_partial_results(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00", "14:00:00"], ["/tmp/lote.xlsx"])
    p.mark_round_started(0)
    p.record_round_result(0, "/tmp/lote.xlsx", 12, "ONLINE", "")

    assert p.first_incomplete_round_index() == 0
    assert p.get_in_progress_round_index() == 0


# --- round results ----------------------------------------------------------


def test_round_results_round_trip_per_excel_path(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/a.xlsx", "/tmp/b.xlsx"])

    p.record_round_result(0, "/tmp/a.xlsx", 12, "ONLINE", "")
    p.record_round_result(0, "/tmp/a.xlsx", 13, "OFFLINE", "NO_RESPONSE_TIMEOUT")
    p.record_round_result(0, "/tmp/b.xlsx", 12, "UNKNOWN", "Marca no soportada")

    assert p.get_round_results(0, "/tmp/a.xlsx") == {
        "12": {"status": "ONLINE", "error": ""},
        "13": {"status": "OFFLINE", "error": "NO_RESPONSE_TIMEOUT"},
    }
    assert p.get_round_results(0, "/tmp/b.xlsx") == {
        "12": {"status": "UNKNOWN", "error": "Marca no soportada"}
    }
    assert p.get_round_results(0, "/tmp/c.xlsx") == {}
    assert p.get_round_results(1, "/tmp/a.xlsx") == {}


# --- aggregate --------------------------------------------------------------


def test_aggregate_round_trip(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/a.xlsx"])

    p.save_aggregate(
        "/tmp/a.xlsx",
        {
            12: _AggregateItem("ONLINE", "", 2),
            13: _AggregateItem("OFFLINE", "NO_RESPONSE_TIMEOUT", 1),
        },
    )

    assert p.get_aggregate("/tmp/a.xlsx") == {
        "12": {"status": "ONLINE", "error": "", "rounds_observed": 2},
        "13": {"status": "OFFLINE", "error": "NO_RESPONSE_TIMEOUT", "rounds_observed": 1},
    }
    assert p.get_aggregate("/tmp/b.xlsx") == {}


def test_aggregate_defaults_for_missing_attrs(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/a.xlsx"])

    p.save_aggregate("/tmp/a.xlsx", {12: object()})

    assert p.get_aggregate("/tmp/a.xlsx") == {
        "12": {"status": "OFFLINE", "error": "", "rounds_observed": 0}
    }


# --- clear ------------------------------------------------------------------


def test_clear_removes_state_file_and_does_not_fail_when_missing(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/lote.xlsx"])
    assert (tmp_path / "run_state.json").exists()

    p.clear()
    assert not (tmp_path / "run_state.json").exists()

    p.clear()
    assert not (tmp_path / "run_state.json").exists()


def test_clear_removes_state_file_and_bak(tmp_path):
    p = _make_persistence(tmp_path)
    p.ensure_day("2026-08-19", ["08:00:00"], ["/tmp/lote.xlsx"])
    p.mark_round_started(0)
    bak = tmp_path / "run_state.json.bak"
    assert (tmp_path / "run_state.json").exists()
    assert bak.exists()

    p.clear()
    assert not (tmp_path / "run_state.json").exists()
    assert not bak.exists()