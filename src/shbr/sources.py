"""Source adapters.

A Source observes one agent runtime. It may expose any subset of three
capabilities; the engine composes whatever is available:

  * meter()         -> a per-run usage snapshot (token/quota providers table,
                       or a per-source aggregate). Return None if unavailable.
  * sessions(hours) -> a list of recent/active session dicts.
  * memory_globs()  -> {label: glob} of persistent-memory files to diff.

All deployment-specific paths enter here from config — never the core.
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import subprocess
from pathlib import Path

from .config import CLAUDE_MEMORY_GLOB
from .util import now, which


class Source:
    name = "base"

    def meter(self):
        return None

    def sessions(self, hours: float):
        return []

    def memory_globs(self) -> dict:
        return {}


# ---------------------------------------------------------------- AgentCat
class AgentCatSource(Source):
    """Public 3rd-party CLI usage aggregator (reference source)."""

    name = "agentcat"

    def __init__(self, cfg: dict):
        self.bin = cfg.get("bin", "agentcat")

    @classmethod
    def available(cls, cfg: dict) -> bool:
        return which(cfg.get("bin", "agentcat"))

    def _snapshot(self):
        try:
            out = subprocess.run(
                [self.bin, "snapshot", "--json"],
                capture_output=True, text=True, timeout=45,
            )
            if out.returncode != 0 or not out.stdout.strip():
                return None
            return json.loads(out.stdout)
        except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
            return None

    def meter(self):
        snap = self._snapshot()
        if not snap:
            return None
        providers = {}
        for name, p in (snap.get("providers") or {}).items():
            tok = p.get("tokens") or {}
            quotas = ((p.get("limits") or {}).get("quotas")) or []
            providers[name] = {
                "status": p.get("status"),
                "today": tok.get("today", 0),
                "week": tok.get("week", 0),
                "month": tok.get("month", 0),
                "all": tok.get("all", 0),
                "quotas": [
                    {
                        "id": q.get("id"),
                        "window": q.get("window"),
                        "remainingPercent": q.get("remainingPercent"),
                        "usedPercent": q.get("usedPercent"),
                    }
                    for q in quotas
                ],
            }
        act = snap.get("activity") or {}
        return {
            "kind": "providers",
            "source": self.name,
            "providers": providers,
            "memory_bytes": act.get("memoryBytesByProvider") or {},
            "process_count": act.get("processCount"),
        }


# ----------------------------------------------------------- Claude memory
class ClaudeMemorySource(Source):
    """Persistent-memory files written by Claude Code (public layout)."""

    name = "claude_memory"

    def __init__(self, cfg: dict):
        self.glob = os.path.expanduser(cfg.get("glob", CLAUDE_MEMORY_GLOB))

    def memory_globs(self) -> dict:
        return {"claude": self.glob}


# ---------------------------------------------------------------- Hermes
class HermesSource(Source):
    """Adapter for a local SQLite-backed agent runtime.

    Opt-in; absent from the default config. Enable it in config.toml where such
    a runtime exists, overriding ``db`` / ``memory_glob`` as needed.
    """

    name = "hermes"

    def __init__(self, cfg: dict):
        self.db = Path(os.path.expanduser(cfg.get("db", "~/.hermes/state.db")))
        self.memory_glob = os.path.expanduser(
            cfg.get("memory_glob", "~/.hermes/memories/*.md")
        )

    def _con(self):
        if not self.db.exists():
            return None
        try:
            return sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            return None

    def meter(self):
        con = self._con()
        if con is None:
            return None
        try:
            cur = con.cursor()
            day_ago = now() - 86400
            week_ago = now() - 7 * 86400
            row = cur.execute(
                """
                SELECT count(*),
                       COALESCE(sum(input_tokens),0), COALESCE(sum(output_tokens),0),
                       COALESCE(sum(cache_read_tokens),0), COALESCE(sum(reasoning_tokens),0),
                       COALESCE(sum(CASE WHEN started_at>? THEN input_tokens+output_tokens ELSE 0 END),0),
                       COALESCE(sum(CASE WHEN started_at>? THEN input_tokens+output_tokens ELSE 0 END),0),
                       COALESCE(sum(actual_cost_usd),0)
                FROM sessions
                """,
                (day_ago, week_ago),
            ).fetchone()
            cost_status = dict(
                cur.execute(
                    "SELECT COALESCE(NULLIF(cost_status,''),'(none)'), count(*) "
                    "FROM sessions GROUP BY 1"
                ).fetchall()
            )
            by_model = cur.execute(
                """SELECT model, count(*), sum(input_tokens+output_tokens)
                   FROM sessions GROUP BY model ORDER BY 3 DESC LIMIT 5"""
            ).fetchall()
            return {
                "kind": "aggregate",
                "source": self.name,
                "sessions": row[0],
                "input": row[1], "output": row[2],
                "cache_read": row[3], "reasoning": row[4],
                "today": row[5], "week": row[6],
                "actual_cost_usd": row[7],
                "cost_status": cost_status,
                "by_model": [
                    {"model": m, "sessions": c, "tokens": t or 0} for m, c, t in by_model
                ],
            }
        except sqlite3.Error:
            return None
        finally:
            con.close()

    def sessions(self, hours: float):
        con = self._con()
        if con is None:
            return []
        try:
            cur = con.cursor()
            since = now() - hours * 3600
            rows = cur.execute(
                """
                SELECT id, source, model, started_at, ended_at, end_reason,
                       message_count, tool_call_count,
                       COALESCE(input_tokens,0)+COALESCE(output_tokens,0),
                       cwd, git_branch, handoff_state
                FROM sessions
                WHERE started_at > ? OR ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 50
                """,
                (since,),
            ).fetchall()
            out = []
            for r in rows:
                out.append({
                    "id": r[0], "runtime": r[1], "model": r[2],
                    "started_at": r[3], "ended_at": r[4], "end_reason": r[5],
                    "messages": r[6], "tool_calls": r[7], "tokens": r[8],
                    "cwd": r[9], "git_branch": r[10], "handoff_state": r[11],
                    "active": r[4] is None,
                })
            return out
        except sqlite3.Error:
            return []
        finally:
            con.close()

    def memory_globs(self) -> dict:
        return {"hermes": self.memory_glob}


REGISTRY = {
    "agentcat": AgentCatSource,
    "claude_memory": ClaudeMemorySource,
    "hermes": HermesSource,
}


def build_sources(cfg) -> list:
    """Instantiate every enabled + available source, in registry order."""
    out = []
    for name, cls in REGISTRY.items():
        sc = cfg.source(name)
        if not sc.get("enabled"):
            continue
        if hasattr(cls, "available") and not cls.available(sc):
            continue
        out.append(cls(sc))
    return out
