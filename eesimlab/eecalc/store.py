"""Calculation history and saved calculations (JSON on disk, GUI-independent)."""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

MAX_HISTORY = 200


@dataclass
class Entry:
    calc_id: str
    title: str
    options: dict
    unknown: str | None
    entries: dict  # key -> [text, unit]
    headline: str = ""
    name: str = ""
    time: str = field(default_factory=lambda: _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.history: list[Entry] = []
        self.saved: list[Entry] = []
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        known = set(Entry.__dataclass_fields__)

        def mk(d):
            return Entry(**{k: v for k, v in d.items() if k in known})

        self.history = [mk(d) for d in data.get("history", []) if isinstance(d, dict) and "calc_id" in d]
        self.saved = [mk(d) for d in data.get("saved", []) if isinstance(d, dict) and "calc_id" in d]

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"history": [asdict(e) for e in self.history],
                                       "saved": [asdict(e) for e in self.saved]}, indent=1, ensure_ascii=False),
                           encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass  # history is a convenience; never crash the calculator over it

    def add_history(self, e: Entry) -> None:
        self.history.insert(0, e)
        del self.history[MAX_HISTORY:]
        self.save()

    def add_saved(self, e: Entry) -> None:
        self.saved.insert(0, e)
        self.save()

    def delete_saved(self, index: int) -> None:
        if 0 <= index < len(self.saved):
            del self.saved[index]
            self.save()

    def clear_history(self) -> None:
        self.history.clear()
        self.save()
