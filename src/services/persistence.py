import json
import time
from pathlib import Path
from typing import Any


class RunPersistence:
    def __init__(self, path: Path, logger):
        self.path = Path(path)
        self.logger = logger
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
        if not isinstance(data, dict) or data.get("version") != 1:
            self.logger.warning(f"Persistencia inválida en {self.path}, se reiniciará.")
            return None
        return data

    def _write(self) -> None:
        if self.state is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
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
            state = {
                "version": 1,
                "day": day_label,
                "run_times": list(run_times),
                "excel_paths": list(excel_paths),
                "rounds": [
                    {"index": idx, "completed": False, "completed_at": None}
                    for idx in range(len(run_times))
                ],
                "current_round": None,
                "round_results": {},
                "aggregate": {},
                "updated_at": int(time.time()),
            }
            self.state = state
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
        except Exception as ex:
            self.logger.warning(f"No se pudo eliminar persistencia ({self.path}): {ex}")

    def _ensure_state(self) -> dict[str, Any]:
        if self.state is None:
            self.state = self._load() or {}
        return self.state

    def get_in_progress_round_index(self) -> int | None:
        state = self._ensure_state()
        current = state.get("current_round")
        if isinstance(current, dict):
            idx = current.get("index")
            if isinstance(idx, int):
                rounds = state.get("rounds") or []
                if 0 <= idx < len(rounds):
                    if not rounds[idx].get("completed"):
                        return idx
        return None

    def first_incomplete_round_index(self) -> int | None:
        state = self._ensure_state()
        rounds = state.get("rounds") or []
        for idx, item in enumerate(rounds):
            if not item.get("completed"):
                return idx
        return None

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

    def _round_bucket(self, round_index: int) -> dict[str, Any]:
        state = self._ensure_state()
        round_results = state.setdefault("round_results", {})
        bucket = round_results.setdefault(str(round_index), {})
        return bucket

    def get_round_results(self, round_index: int, excel_path: str) -> dict[str, dict[str, Any]]:
        bucket = self._round_bucket(round_index)
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
        bucket = self._round_bucket(round_index)
        results = bucket.setdefault(excel_path, {})
        idx_key = str(row_index)
        results[idx_key] = {"status": status, "error": error_code}
        self._ensure_state()["updated_at"] = int(time.time())
        self._write()

    def get_aggregate(self, excel_path: str) -> dict[str, dict[str, Any]]:
        state = self._ensure_state()
        agg = state.get("aggregate", {}).get(excel_path)
        if isinstance(agg, dict):
            return agg
        return {}

    def save_aggregate(self, excel_path: str, aggregate: dict[object, Any]) -> None:
        state = self._ensure_state()
        data: dict[str, dict[str, Any]] = {}
        for key, item in aggregate.items():
            data[str(key)] = {
                "status": getattr(item, "status", "OFFLINE"),
                "error": getattr(item, "error", ""),
                "rounds_observed": int(getattr(item, "rounds_observed", 0) or 0),
            }
        state.setdefault("aggregate", {})[excel_path] = data
        state["updated_at"] = int(time.time())
        self._write()
