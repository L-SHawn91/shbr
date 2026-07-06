"""Local state: an append-only JSONL event log and a memory-index snapshot.

Never a live database. Metadata only — never prompt or memory content.
"""
from __future__ import annotations

import json
import shutil

from .config import Config
from .util import now


class State:
    def __init__(self, cfg: Config):
        self.dir = cfg.state_dir
        self.events = self.dir / "events.jsonl"
        self.index = self.dir / "memory_index.json"
        self.migrate_from = cfg.migrate_from
        self._ready = False

    def ensure(self) -> None:
        if self._ready:
            return
        if not self.dir.exists():
            self.dir.mkdir(parents=True, exist_ok=True)
            # optional one-time migration to preserve a prior diff baseline
            if self.migrate_from and self.migrate_from.exists():
                for name in ("events.jsonl", "memory_index.json"):
                    src = self.migrate_from / name
                    if src.exists():
                        shutil.copy2(src, self.dir / name)
        self._ready = True

    def load_index(self) -> dict:
        self.ensure()
        if self.index.exists():
            try:
                return json.loads(self.index.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def save_index(self, inv: dict) -> None:
        self.ensure()
        self.index.write_text(json.dumps(inv, indent=1))

    def append_event(self, ev: dict) -> None:
        self.ensure()
        ev = {"ts": round(now(), 3), **ev}
        with self.events.open("a") as fh:
            fh.write(json.dumps(ev) + "\n")
