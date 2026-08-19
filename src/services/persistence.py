"""JSON run-state persistence for the daily SMS rounds.

State machine: ensure_day opens a day -> rounds run in order, each
recording per-device results in round_results -> after each round the
aggregate is updated -> at day close the state is cleared. A crash
mid-round leaves the state on disk so the next start resumes from the
last recorded device.

Schema: version 2 (v1 files load fine and are upgraded on the next
write). Writes are atomic (.tmp + replace) and keep a .bak of the
previous file next to the state file.
"""

import copy
import json
import shutil
import time
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
SUPPORTED_VERSIONS = {1, 2}


def _blank_state() -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "day": "",
        "run_times": [],
        "excel_paths": [],
        "rounds": [],
        "current_round": None,
        "round_results": {},
        "aggregate": {},
        "updated_at": int(time.time()),
    }


def new_day_state(day_label: str, run_times: list[str], excel_paths: list[str]) -> dict[str, Any]:
    state = _blank_state()
    state["day"] = day_label
    state["run_times"] = list(run_times)
    state["excel_paths"] = list(excel_paths)
    state["rounds"] = [
        {"index": idx, "completed": False, "completed_at": None}
        for idx in range(len(run_times))
    ]
    return state


def _is_valid_state(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("version") not in SUPPORTED_VERSIONS:
        return False
    if not isinstance(data.get("day"), str):
        return False
    run_times = data.get("run_times")
    if not isinstance(run_times, list) or not all(isinstance(t, str) for t in run_times):
        return False
    rounds = data.get("rounds")
    if not isinstance(rounds, list) or not all(isinstance(r, dict) for r in rounds):
        return False
    if not isinstance(data.get("round_results"), dict):
        return False
    if not isinstance(data.get("aggregate"), dict):
        return False
    return True


def record_round_result_state(
    state: dict[str, Any],
    round_index: int,
    excel_path: str,
    row_index: object,
    status: str,
    error_code: str,
) -> dict[str, Any]:
    new_state = copy.deepcopy(state)
    round_results = new_state.setdefault("round_results", {})
    bucket = round_results.setdefault(str(round_index), {})
    results = bucket.setdefault(excel_path, {})
    results[str(row_index)] = {"status": status, "error": error_code}
    return new_state


def save_aggregate_state(
    state: dict[str, Any],
    excel_path: str,
    aggregate: dict[Any, Any],
) -> dict[str, Any]:
    new_state = copy.deepcopy(state)
    data: dict[str, dict[str, Any]] = {}
    for key, item in aggregate.items():
        data[str(key)] = {
            "status": getattr(item, "status", "OFFLINE"),
            "error": getattr(item, "error", ""),
            "rounds_observed": int(getattr(item, "rounds_observed", 0) or 0),
        }
    new_state.setdefault("aggregate", {})[excel_path] = data
    return new_state


def first_incomplete_round_index(state: dict[str, Any]) -> int | None:
    rounds = state.get("rounds") or []
    for idx, item in enumerate(rounds):
        if not item.get("completed"):
            return idx
    return None


def in_progress_round_index(state: dict[str, Any]) -> int | None:
    current = state.get("current_round")
    if isinstance(current, dict):
        idx = current.get("index")
        if isinstance(idx, int):
            rounds = state.get("rounds") or []
            if 0 <= idx < len(rounds) and not rounds[idx].get("completed"):
                return idx
    return None


class RunPersistence:
    def __init__(self, path: Path, logger):
        self.path = Path(path)
        self.logger = logger.bind(component="persistence")
        self.state: dict[str, Any] | None = None

    def _load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except Exception as ex:
            self.logger.warning(f"No se pudo leer persistencia ({self.path}): {ex}")
            return None
        if not _is_valid_state(data):
            self.logger.warning(f"Persistencia inválida en {self.path}, se reiniciará.")
            return None
        return data

    def _write(self) -> None:
        if self.state is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists():
                shutil.copy2(self.path, self.path.with_suffix(self.path.suffix + ".bak"))
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            self.state["version"] = SCHEMA_VERSION
            tmp_path.write_text(json.dumps(self.state, ensure_ascii=True, indent=2), encoding="utf-8")
            tmp_path.replace(self.path)
        except Exception as ex:
            self.logger.warning(f"No se pudo guardar persistencia ({self.path}): {ex}")

    def ensure_day(self, day_label: str, run_times: list[str], excel_paths: list[str]) -> None:
        state = self._load()
        reset = True
        if state:
            if state.get("day") == day_label and state.get("run_times") == run_times:
                reset = False

        if reset:
            self.state = new_day_state(day_label, run_times, excel_paths)
            self._write()
            return

        if state is None:
            return
        state["excel_paths"] = list(excel_paths)
        state["updated_at"] = int(time.time())
        self.state = state
        self._write()

    def clear(self) -> None:
        self.state = None
        try:
            if self.path.exists():
                self.path.unlink()
            bak_path = self.path.with_suffix(self.path.suffix + ".bak")
            if bak_path.exists():
                bak_path.unlink()
        except Exception as ex:
            self.logger.warning(f"No se pudo eliminar persistencia ({self.path}): {ex}")

    def _ensure_state(self) -> dict[str, Any]:
        if self.state is None:
            self.state = self._load()
            if self.state is None:
                self.state = _blank_state()
        return self.state

    def get_in_progress_round_index(self) -> int | None:
        return in_progress_round_index(self._ensure_state())

    def first_incomplete_round_index(self) -> int | None:
        return first_incomplete_round_index(self._ensure_state())

    def mark_round_started(self, round_index: int) -> None:
        state = self._ensure_state()
        state["current_round"] = {"index": round_index, "started_at": int(time.time())}
        state["updated_at"] = int(time.time())
        self._write()

    def mark_round_completed(self, round_index: int) -> None:
        state = self._ensure_state()
        rounds = state.get("rounds") or []
        if 0 <= round_index < len(rounds):
            rounds[round_index]["completed"] = True
            rounds[round_index]["completed_at"] = int(time.time())
        state["current_round"] = None
        state["updated_at"] = int(time.time())
        self._write()

    def get_round_results(self, round_index: int, excel_path: str) -> dict[str, dict[str, Any]]:
        state = self._ensure_state()
        bucket = state.get("round_results", {}).get(str(round_index))
        if isinstance(bucket, dict):
            results = bucket.get(excel_path)
            if isinstance(results, dict):
                return results
        return {}

    def record_round_result(
        self,
        round_index: int,
        excel_path: str,
        row_index: object,
        status: str,
        error_code: str,
    ) -> None:
        state = record_round_result_state(
            self._ensure_state(), round_index, excel_path, row_index, status, error_code
        )
        state["updated_at"] = int(time.time())
        self.state = state
        self._write()

    def get_aggregate(self, excel_path: str) -> dict[str, dict[str, Any]]:
        state = self._ensure_state()
        agg = state.get("aggregate", {}).get(excel_path)
        if isinstance(agg, dict):
            return agg
        return {}

    def save_aggregate(self, excel_path: str, aggregate: dict[object, Any]) -> None:
        state = save_aggregate_state(self._ensure_state(), excel_path, aggregate)
        state["updated_at"] = int(time.time())
        self.state = state
        self._write()
