"""Persistent per-run logs: data/runs/<YYYY-MM-DD_HHMM>/ (data/ is git-ignored, it holds personal data)."""
from __future__ import annotations

import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .data import DATA_DIR


class Tee:
    """File-like object writing to the original stream and to the log file."""

    def __init__(self, stream, fh):
        self.stream, self.fh = stream, fh

    def write(self, text: str) -> int:
        try:
            self.stream.write(text)
        except Exception:  # noqa: BLE001 - never let the console break logging
            pass
        self.fh.write(text)
        self.fh.flush()
        return len(text)

    def flush(self) -> None:
        try:
            self.stream.flush()
        except Exception:  # noqa: BLE001
            pass
        self.fh.flush()

    def __getattr__(self, name: str):
        return getattr(self.stream, name)


class RunDir:
    """Context manager: creates the run directory and tees stdout/stderr/logging into run.log."""

    def __init__(self, base: Path | None = None, now: dt.datetime | None = None):
        base = base or DATA_DIR / "runs"
        stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d_%H%M")
        path, n = base / stamp, 1
        while path.exists():
            n += 1
            path = base / f"{stamp}_{n}"
        path.mkdir(parents=True)
        self.path = path
        self._fh = None

    def __enter__(self) -> "RunDir":
        self._fh = open(self.path / "run.log", "a", encoding="utf-8")
        self._old = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = Tee(sys.stdout, self._fh), Tee(sys.stderr, self._fh)
        # dspy's own loggers write to whatever sys.stderr is at call time, so they land in run.log too;
        # this root handler adds ptah.* (Qloo retries, ...).
        self._handler = logging.StreamHandler(sys.stderr)
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root = logging.getLogger()
        self._root_level = root.level
        root.addHandler(self._handler)
        root.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc) -> None:
        root = logging.getLogger()
        root.removeHandler(self._handler)
        root.setLevel(self._root_level)
        sys.stdout, sys.stderr = self._old
        if self._fh:
            self._fh.close()

    def write_json(self, name: str, obj: Any) -> Path:
        p = self.path / name
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        return p

    def write_text(self, name: str, text: str) -> Path:
        p = self.path / name
        p.write_text(text, encoding="utf-8")
        return p
