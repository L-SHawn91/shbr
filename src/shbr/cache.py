"""Short-TTL on-disk cache for opt-in connector results.

A connector fetch is a live network round-trip, but the menu bar refreshes far
more often than a provider's rolling quota window actually moves. This caches
each connector's last successful result for a few minutes so a tight refresh
reads disk instead of re-hitting the provider — and, when a fetch briefly fails,
it keeps the last good value on screen instead of flickering to blank.

Metadata only: the cached payload is exactly the remaining-quota dict the
connector returns (windows + percentages). No tokens, no secrets — the connector
never returns those, so this only ever stores what it returns. Fail-silent: any
read/write error degrades to a live fetch, never an exception.
"""
from __future__ import annotations

import json

from .util import now

DEFAULT_TTL = 300.0  # 5 minutes — matches agentcat's live-limits-cache window


class ConnectorCache:
    """Per-connector positive cache backed by one JSON file in the state dir."""

    def __init__(self, state_dir, ttl: float = DEFAULT_TTL):
        self.path = state_dir / "connector-cache.json"
        self.ttl = ttl
        self._data = None

    def _load(self) -> dict:
        if self._data is None:
            try:
                d = json.loads(self.path.read_text())
                self._data = d if isinstance(d, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def fresh(self, name: str):
        """Cached result younger than the TTL, else None (a live fetch is due)."""
        e = self._load().get(name)
        if isinstance(e, dict) and (now() - e.get("ts", 0)) < self.ttl:
            return e.get("data")
        return None

    def stale(self, name: str):
        """Last cached result at any age — the fetch-failure fallback."""
        e = self._load().get(name)
        return e.get("data") if isinstance(e, dict) else None

    def put(self, name: str, data) -> None:
        d = self._load()
        d[name] = {"ts": round(now(), 3), "data": data}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(d, indent=1))
        except OSError:
            pass
