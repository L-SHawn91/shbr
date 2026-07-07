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
import sys
import time
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


# ------------------------------------------------------------------ Usage
class UsageSource(Source):
    """Native per-provider token reader — screens every known agent, shows
    only the ones that are actually active on this machine.

    Each run screens the full known-provider set (see ``_readers``). A provider
    reader reads that agent's own on-disk usage ledger directly — no external
    aggregator process — and returns a totals dict, or ``None`` when the agent
    is not installed / has no readable local usage. Only non-``None`` providers
    are emitted: an absent or idle agent is simply omitted, never surfaced as a
    "not found" placeholder. Adding a provider is one entry in ``_readers``.

    Strictly read-only: SQLite is opened ``mode=ro`` and JSON/JSONL caches are
    only read, never written. Token totals (today/week/month/all) are computed
    locally from those files.

    Per-window *remaining quotas* are deliberately omitted: they are not
    persisted on disk — they are live values a provider only returns from an
    authenticated API call — and shbr reports a quota number only when it is
    actually present. So ``quotas`` stays empty rather than fabricated.

    Providers screened (only those with readable local usage are emitted):

      * codex  -> ~/.codex/state_5.sqlite     (threads.tokens_used, updated_at)
      * claude -> ~/.claude/stats-cache.json  (dailyModelTokens[].tokensByModel)
      * gemini -> ~/.gemini/tmp/*/chats/*.jsonl (usageMetadata.totalTokenCount)
    """

    name = "usage"

    def __init__(self, cfg: dict):
        self.codex_db = os.path.expanduser(
            cfg.get("codex_db", "~/.codex/state_5.sqlite"))
        self.claude_stats = os.path.expanduser(
            cfg.get("claude_stats", "~/.claude/stats-cache.json"))
        self.gemini_dir = os.path.expanduser(
            cfg.get("gemini_dir", "~/.gemini"))

    def _readers(self):
        """(provider_name, reader) for every known agent, screened each run."""
        return (
            ("codex", self._codex),
            ("claude", self._claude),
            ("gemini", self._gemini),
        )

    @classmethod
    def available(cls, cfg: dict) -> bool:
        # Load as long as *any* known agent leaves state on disk. Per-provider
        # screening in meter() then decides which ones actually surface.
        paths = (
            cfg.get("codex_db", "~/.codex/state_5.sqlite"),
            cfg.get("claude_stats", "~/.claude/stats-cache.json"),
            cfg.get("gemini_dir", "~/.gemini"),
        )
        return any(os.path.exists(os.path.expanduser(p)) for p in paths)

    @staticmethod
    def _totals(all_, today, week, month):
        return {
            "status": "ok" if all_ else "idle",
            "today": today, "week": week, "month": month, "all": all_,
            "quotas": [],
        }

    # -- codex: sum thread token counters by last-activity window ------
    def _codex(self):
        if not os.path.exists(self.codex_db):
            return None
        try:
            con = sqlite3.connect(f"file:{self.codex_db}?mode=ro",
                                  uri=True, timeout=5)
        except sqlite3.Error:
            return None
        try:
            t = now()
            row = con.execute(
                """
                SELECT COALESCE(SUM(tokens_used), 0),
                       COALESCE(SUM(CASE WHEN updated_at > ? THEN tokens_used END), 0),
                       COALESCE(SUM(CASE WHEN updated_at > ? THEN tokens_used END), 0),
                       COALESCE(SUM(CASE WHEN updated_at > ? THEN tokens_used END), 0)
                FROM threads
                """,
                (t - 86400, t - 7 * 86400, t - 30 * 86400),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            con.close()
        if not row:
            return None
        all_, today, week, month = row
        return self._totals(all_, today, week, month)

    # -- claude: sum per-day model token buckets by date ---------------
    def _claude(self):
        if not os.path.exists(self.claude_stats):
            return None
        try:
            with open(self.claude_stats, "rb") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return None
        daily = data.get("dailyModelTokens") or []
        t = now()
        today_str = time.strftime("%Y-%m-%d", time.localtime(t))
        b = {"today": 0, "week": 0, "month": 0, "all": 0}
        for entry in daily:
            if not isinstance(entry, dict):
                continue
            tokens = sum(v for v in (entry.get("tokensByModel") or {}).values()
                         if isinstance(v, (int, float)))
            b["all"] += tokens
            date = entry.get("date")
            try:
                ts = time.mktime(time.strptime(date, "%Y-%m-%d"))
            except (ValueError, TypeError, OverflowError):
                continue
            if date == today_str:
                b["today"] += tokens
            if ts > t - 7 * 86400:
                b["week"] += tokens
            if ts > t - 30 * 86400:
                b["month"] += tokens
        return self._totals(b["all"], b["today"], b["week"], b["month"])

    # -- gemini: sum per-turn usageMetadata across session transcripts -
    def _gemini(self):
        # Gemini CLI keeps no aggregate ledger; token usage, when present, is
        # stamped per turn as usageMetadata.totalTokenCount inside the session
        # JSONL. Absent that (telemetry off) the agent is simply not surfaced.
        chats = os.path.join(self.gemini_dir, "tmp", "*", "chats", "*.jsonl")
        files = glob.glob(chats)
        if not files:
            return None
        t = now()
        b = {"today": 0, "week": 0, "month": 0, "all": 0}
        seen = False
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for line in lines:
                line = line.strip()
                if not line or "totalTokenCount" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                tokens = _find_total_tokens(obj)
                if not tokens:
                    continue
                seen = True
                b["all"] += tokens
                ts = _epoch(obj.get("timestamp"))
                if ts is None:
                    continue
                if ts > t - 86400:
                    b["today"] += tokens
                if ts > t - 7 * 86400:
                    b["week"] += tokens
                if ts > t - 30 * 86400:
                    b["month"] += tokens
        if not seen:
            return None
        return self._totals(b["all"], b["today"], b["week"], b["month"])

    def meter(self):
        providers = {}
        for name, fn in self._readers():
            try:
                p = fn()
            except Exception:
                p = None  # one bad reader must never blank the whole screen
            if p is not None:
                providers[name] = p
        if not providers:
            return None
        return {
            "kind": "providers",
            "source": self.name,
            "providers": providers,
            "memory_bytes": {},
            "process_count": None,
        }


def _find_total_tokens(obj):
    """Best-effort pull of a usageMetadata.totalTokenCount from a Gemini turn."""
    if isinstance(obj, dict):
        um = obj.get("usageMetadata")
        if isinstance(um, dict):
            v = um.get("totalTokenCount")
            if isinstance(v, (int, float)):
                return v
        total = 0
        for v in obj.values():
            total += _find_total_tokens(v) or 0
        return total or None
    if isinstance(obj, list):
        total = 0
        for v in obj:
            total += _find_total_tokens(v) or 0
        return total or None
    return None


def _epoch(value):
    """Coerce a unix-seconds number or ISO-8601 string to unix seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("Z", "+0000")
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return time.mktime(time.strptime(s, fmt))
            except (ValueError, OverflowError):
                continue
    return None


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


# ---------------------------------------------------------------- System
class SystemSource(Source):
    """Host resource observer — CPU, memory, temperature.

    Portable and read-only: reads kernel counters via stdlib plus a couple of
    standard OS utilities (``vm_stat`` / ``sysctl`` on macOS, ``/proc`` on
    Linux). No sudo, no third-party deps. CPU utilisation is a real short-window
    sample on Linux and a load-average proxy elsewhere. Temperature is
    best-effort and may be unavailable without a helper (e.g. osx-cpu-temp).
    """

    name = "system"

    def __init__(self, cfg: dict):
        # optional command that prints a temperature in °C, e.g. "osx-cpu-temp"
        self.temp_cmd = cfg.get("temp_cmd")

    # -- helpers ------------------------------------------------------
    @staticmethod
    def _run(cmd, timeout=5):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return out.stdout if out.returncode == 0 else None
        except (subprocess.SubprocessError, OSError):
            return None

    def _sysctl_int(self, key):
        out = self._run(["sysctl", "-n", key])
        try:
            return int(out.strip()) if out else None
        except ValueError:
            return None

    # -- cpu ----------------------------------------------------------
    def _cpu(self):
        ncpu = os.cpu_count() or 1
        try:
            load1, load5, load15 = os.getloadavg()
        except (OSError, AttributeError):
            load1 = load5 = load15 = None
        util = self._cpu_percent_linux() if sys.platform.startswith("linux") else None
        if util is None and load1 is not None:
            util = round(100 * load1 / ncpu, 1)  # saturation proxy
        return {"ncpu": ncpu, "load1": load1, "load5": load5,
                "load15": load15, "util_pct": util}

    def _cpu_percent_linux(self):
        def snap():
            try:
                first = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            except (OSError, IndexError):
                return None
            vals = [int(x) for x in first]
            idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
            return sum(vals), idle
        a = snap()
        if a is None:
            return None
        time.sleep(0.12)
        b = snap()
        if b is None:
            return None
        dt, di = b[0] - a[0], b[1] - a[1]
        return round(100 * (dt - di) / dt, 1) if dt > 0 else None

    # -- memory -------------------------------------------------------
    def _memory(self):
        if sys.platform == "darwin":
            return self._memory_darwin()
        if sys.platform.startswith("linux"):
            return self._memory_linux()
        return None

    def _memory_darwin(self):
        total = self._sysctl_int("hw.memsize")
        page = self._sysctl_int("hw.pagesize") or 4096
        vm = self._run(["vm_stat"])
        if not vm:
            return {"total": total} if total else None
        stats = {}
        for line in vm.splitlines():
            k, sep, v = line.partition(":")
            if not sep:
                continue
            v = v.strip().rstrip(".")
            if v.isdigit():
                stats[k.strip()] = int(v) * page
        # Activity-Monitor-style "used" ≈ active + wired + compressed
        used = (stats.get("Pages active", 0) + stats.get("Pages wired down", 0)
                + stats.get("Pages occupied by compressor", 0))
        avail = (stats.get("Pages free", 0) + stats.get("Pages inactive", 0)
                 + stats.get("Pages speculative", 0))
        return {"total": total, "used": used or None, "available": avail or None,
                "used_pct": round(100 * used / total, 1) if (total and used) else None}

    def _memory_linux(self):
        try:
            text = Path("/proc/meminfo").read_text()
        except OSError:
            return None
        kv = {}
        for line in text.splitlines():
            k, _, v = line.partition(":")
            kv[k.strip()] = v.strip()

        def kb(key):
            field = kv.get(key, "").split()
            return int(field[0]) * 1024 if field and field[0].isdigit() else None

        total, avail = kb("MemTotal"), kb("MemAvailable")
        used = total - avail if (total and avail is not None) else None
        return {"total": total, "used": used, "available": avail,
                "used_pct": round(100 * used / total, 1) if (total and used) else None}

    # -- temperature --------------------------------------------------
    def _temperature(self):
        candidates = []
        if self.temp_cmd:
            candidates.append(self.temp_cmd.split())
        if sys.platform == "darwin":
            candidates += [["osx-cpu-temp"], ["smctemp", "-c"],
                           ["istats", "cpu", "temp", "--value-only"]]
        elif sys.platform.startswith("linux"):
            candidates += [["sensors", "-u"]]
        for cmd in candidates:
            if not which(cmd[0]):
                continue
            val = self._parse_temp(self._run(cmd))
            if val is not None:
                return val
        if sys.platform.startswith("linux"):
            try:
                raw = Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()
                if raw.lstrip("-").isdigit():
                    return round(int(raw) / 1000, 1)
            except OSError:
                pass
        return None

    @staticmethod
    def _parse_temp(text):
        if not text:
            return None
        import re
        # first plausible temperature reading in the output
        for m in re.finditer(r"(-?\d+(?:\.\d+)?)", text):
            v = float(m.group(1))
            if 10.0 <= v <= 130.0:
                return round(v, 1)
        return None

    def meter(self):
        return {
            "kind": "system",
            "source": self.name,
            "cpu": self._cpu(),
            "memory": self._memory(),
            "temperature_c": self._temperature(),
        }


REGISTRY = {
    "usage": UsageSource,
    "claude_memory": ClaudeMemorySource,
    "system": SystemSource,
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
