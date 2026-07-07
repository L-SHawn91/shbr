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
        self.opencode_db = os.path.expanduser(
            cfg.get("opencode_db", "~/.local/share/opencode/opencode.db"))

    def _readers(self):
        """(provider_name, reader) for every known agent, screened each run."""
        return (
            ("codex", self._codex),
            ("claude", self._claude),
            ("gemini", self._gemini),
            ("opencode", self._opencode),
        )

    @classmethod
    def available(cls, cfg: dict) -> bool:
        # Load as long as *any* known agent leaves state on disk. Per-provider
        # screening in meter() then decides which ones actually surface.
        paths = (
            cfg.get("codex_db", "~/.codex/state_5.sqlite"),
            cfg.get("claude_stats", "~/.claude/stats-cache.json"),
            cfg.get("gemini_dir", "~/.gemini"),
            cfg.get("opencode_db", "~/.local/share/opencode/opencode.db"),
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

    # -- opencode: sum per-message token counts by last-activity window -
    def _opencode(self):
        # opencode (local AI coding agent) stores each turn in a SQLite ledger;
        # assistant messages stamp usage as JSON $.tokens.total with the row's
        # time_created in epoch *milliseconds*. Read metadata only (role/tokens/
        # timestamp) — the secret-bearing account/credential tables are never
        # touched. Opened strictly read-only.
        if not os.path.exists(self.opencode_db):
            return None
        try:
            con = sqlite3.connect(f"file:{self.opencode_db}?mode=ro",
                                  uri=True, timeout=5)
        except sqlite3.Error:
            return None
        try:
            t_ms = now() * 1000
            row = con.execute(
                """
                SELECT
                  COALESCE(SUM(CAST(json_extract(data,'$.tokens.total') AS INTEGER)), 0),
                  COALESCE(SUM(CASE WHEN time_created > ?
                    THEN CAST(json_extract(data,'$.tokens.total') AS INTEGER) END), 0),
                  COALESCE(SUM(CASE WHEN time_created > ?
                    THEN CAST(json_extract(data,'$.tokens.total') AS INTEGER) END), 0),
                  COALESCE(SUM(CASE WHEN time_created > ?
                    THEN CAST(json_extract(data,'$.tokens.total') AS INTEGER) END), 0)
                FROM message
                WHERE json_extract(data,'$.role') = 'assistant'
                  AND json_extract(data,'$.tokens.total') IS NOT NULL
                """,
                (t_ms - 86400_000, t_ms - 7 * 86400_000, t_ms - 30 * 86400_000),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            con.close()
        if not row:
            return None
        all_, today, week, month = row
        return self._totals(all_, today, week, month)

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


# ----------------------------------------------------------- Antigravity
class AntigravitySource(Source):
    """Local session/activity reader for Antigravity IDE (Google's agentic
    IDE on Gemini). Antigravity keeps its own conversation store, separate
    from the plain Gemini CLI, so shbr's usage sources never see its activity.

    Metadata only: session count, recency, workspace, title / step-count —
    never token totals or message content (those live in opaque per-conversation
    blobs; remaining quota comes from the antigravity *connector*, not here).
    Opt-in and absent from the default config; SQLite is opened read-only.
    """

    name = "antigravity"

    DEF_HISTORY = "~/.gemini/antigravity-cli/history.jsonl"
    DEF_SUMMARIES = "~/.gemini/antigravity-cli/conversation_summaries.db"

    def __init__(self, cfg: dict):
        self.history = os.path.expanduser(cfg.get("history", self.DEF_HISTORY))
        self.summaries = os.path.expanduser(cfg.get("summaries", self.DEF_SUMMARIES))
        # a conversation whose last activity is within this window reads "active"
        self.active_window = float(cfg.get("active_window_s", 900))

    @classmethod
    def available(cls, cfg: dict) -> bool:
        paths = (cfg.get("history", cls.DEF_HISTORY),
                 cfg.get("summaries", cls.DEF_SUMMARIES))
        return any(os.path.exists(os.path.expanduser(p)) for p in paths)

    @staticmethod
    def _ts(value):
        """Antigravity stamps are ms-epoch ints (history.jsonl) or datetime
        strings (summaries db). Normalize both to unix seconds; None on junk."""
        if isinstance(value, (int, float)):
            # history.jsonl records milliseconds since epoch
            return value / 1000.0 if value > 1e12 else float(value)
        if isinstance(value, str):
            s = value.strip()
            if not s:
                return None
            e = _epoch(s)
            if e is not None:
                return e
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    return time.mktime(time.strptime(s, fmt))
                except (ValueError, OverflowError):
                    continue
        return None

    def _from_history(self) -> dict:
        """conversation_id -> {first, last, workspace, messages} from the
        activity log. Fail-silent on any read / parse error."""
        out = {}
        try:
            with open(self.history, encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return out
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            cid = obj.get("conversationId")
            if not cid:
                continue
            ts = self._ts(obj.get("timestamp"))
            rec = out.setdefault(
                cid, {"first": ts, "last": ts,
                      "workspace": obj.get("workspace"), "messages": 0})
            rec["messages"] += 1
            if ts is not None:
                rec["first"] = ts if rec["first"] is None else min(rec["first"], ts)
                rec["last"] = ts if rec["last"] is None else max(rec["last"], ts)
            if obj.get("workspace"):
                rec["workspace"] = obj["workspace"]
        return out

    def _from_summaries(self) -> dict:
        """conversation_id -> {title, steps, workspace, status, last} from the
        SQLite summary registry (read-only). Empty on any error."""
        out = {}
        if not os.path.exists(self.summaries):
            return out
        try:
            con = sqlite3.connect(f"file:{self.summaries}?mode=ro",
                                  uri=True, timeout=5)
        except sqlite3.Error:
            return out
        try:
            rows = con.execute(
                "SELECT conversation_id, title, step_count, workspace_uris, "
                "status, last_user_input_time, last_modified_time "
                "FROM conversation_summaries"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        for cid, title, steps, ws, status, last_in, last_mod in rows:
            if not cid:
                continue
            out[cid] = {"title": title, "steps": steps, "workspace": ws,
                        "status": status,
                        "last": self._ts(last_in) or self._ts(last_mod)}
        return out

    def sessions(self, hours: float):
        hist = self._from_history()
        summ = self._from_summaries()
        ids = set(hist) | set(summ)
        if not ids:
            return []
        t = now()
        since = t - hours * 3600
        out = []
        for cid in ids:
            h = hist.get(cid, {})
            s = summ.get(cid, {})
            last = s.get("last") or h.get("last")
            started = h.get("first") or last
            if last is not None and last < since:
                continue
            out.append({
                "id": cid,
                "source": self.name,
                "runtime": self.name,
                "model": None,
                "started_at": started,
                "last_at": last,
                "ended_at": None,
                "messages": h.get("messages"),
                "steps": s.get("steps"),
                "tokens": None,  # metadata only — no token totals here
                "cwd": s.get("workspace") or h.get("workspace"),
                "title": s.get("title"),
                "status": s.get("status"),
                "active": last is not None and last > t - self.active_window,
            })
        out.sort(key=lambda r: (r.get("last_at") or 0), reverse=True)
        return out[:50]


# ------------------------------------------------------ Claude Code sessions
class ClaudeSessionSource(Source):
    """Session reader for Claude Code's own transcripts.

    Claude Code writes one JSONL transcript per session under
    ``~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl``. The plain
    ``usage`` source only reads Claude's aggregate token *ledger*; nothing else
    surfaces these as individual sessions — so real Claude Code work never
    appeared in the sessions list (only Hermes-orchestrated runs did).

    Metadata only: session id, model, workspace, first/last activity, message
    count, and summed input+output tokens computed locally from the transcript.
    Message *content* is never read into the payload. Files are only read, never
    written; stale transcripts (older than the window by mtime) are skipped so a
    refresh only touches recently-active sessions.
    """

    name = "claude_sessions"
    DEF_GLOB = "~/.claude/projects/*/*.jsonl"

    def __init__(self, cfg: dict):
        self.glob = os.path.expanduser(cfg.get("glob", self.DEF_GLOB))
        self.active_window = float(cfg.get("active_window_s", 900))

    @classmethod
    def available(cls, cfg: dict) -> bool:
        return bool(glob.glob(os.path.expanduser(cfg.get("glob", cls.DEF_GLOB))))

    def _parse(self, path: str) -> dict | None:
        """Fold one transcript into a metadata record. None on unreadable."""
        first = last = None
        model = None
        messages = 0
        tokens = 0
        cwd = None
        sid = None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    sid = obj.get("sessionId") or sid
                    if obj.get("cwd"):
                        cwd = obj["cwd"]
                    ts = _epoch(obj.get("timestamp"))
                    if ts is not None:
                        first = ts if first is None else min(first, ts)
                        last = ts if last is None else max(last, ts)
                    if obj.get("type") in ("user", "assistant"):
                        messages += 1
                    msg = obj.get("message")
                    if isinstance(msg, dict):
                        if msg.get("model"):
                            model = msg["model"]
                        usage = msg.get("usage")
                        if isinstance(usage, dict):
                            tokens += (usage.get("input_tokens") or 0) + (
                                usage.get("output_tokens") or 0)
        except OSError:
            return None
        if sid is None:
            sid = os.path.splitext(os.path.basename(path))[0]
        return {"id": sid, "model": model, "cwd": cwd, "first": first,
                "last": last, "messages": messages, "tokens": tokens or None}

    def sessions(self, hours: float):
        t = now()
        since = t - hours * 3600
        out = []
        for path in glob.glob(self.glob):
            try:
                if os.path.getmtime(path) < since:
                    continue
            except OSError:
                continue
            rec = self._parse(path)
            if rec is None:
                continue
            last = rec["last"]
            if last is not None and last < since:
                continue
            out.append({
                "id": rec["id"],
                "source": "claude",   # display label — the runtime, not the config key
                "runtime": "claude",
                "model": rec["model"],
                "started_at": rec["first"] or last,
                "last_at": last,
                "ended_at": None,
                "messages": rec["messages"],
                "tokens": rec["tokens"],
                "cwd": rec["cwd"],
                "active": last is not None and last > t - self.active_window,
            })
        out.sort(key=lambda r: (r.get("last_at") or 0), reverse=True)
        return out[:50]


# ---------------------------------------------------------------- Cursor
class CursorSource(Source):
    """Session reader for the Cursor IDE agent.

    Cursor keeps its composer (chat/agent) sessions in a SQLite key-value store
    at ``~/Library/Application Support/Cursor/User/globalStorage/state.vscdb``:
    one ``composerData:<uuid>`` row per session. shbr's usage/token sources
    never see Cursor activity, so it goes here.

    Metadata only: composer id, mode/model, title, created/updated time, and
    message count (from the conversation header list). Message *content* lives in
    separate ``bubbleId:*`` rows and is never read. SQLite is opened read-only.
    Opt-in via ``available()`` — silently absent when Cursor isn't installed.
    """

    name = "cursor"
    DEF_DB = ("~/Library/Application Support/Cursor/User/"
              "globalStorage/state.vscdb")

    def __init__(self, cfg: dict):
        self.db = os.path.expanduser(cfg.get("db", self.DEF_DB))
        self.active_window = float(cfg.get("active_window_s", 900))

    @classmethod
    def available(cls, cfg: dict) -> bool:
        return os.path.exists(os.path.expanduser(cfg.get("db", cls.DEF_DB)))

    @staticmethod
    def _ms(v):
        """Cursor stamps are ms-epoch ints. Normalize to unix seconds."""
        if isinstance(v, (int, float)) and v > 0:
            return v / 1000.0 if v > 1e12 else float(v)
        return None

    @staticmethod
    def _cwd(d: dict):
        for r in (d.get("trackedGitRepos") or []):
            if isinstance(r, dict):
                u = r.get("rootUri") or r.get("repoRoot")
                if u:
                    return u
        return None

    def sessions(self, hours: float):
        if not os.path.exists(self.db):
            return []
        try:
            con = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=5)
        except sqlite3.Error:
            return []
        try:
            rows = con.execute(
                "SELECT value FROM cursorDiskKV WHERE key LIKE 'composerData:%'"
            ).fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            con.close()
        t = now()
        since = t - hours * 3600
        out = []
        for (val,) in rows:
            try:
                d = json.loads(val)
            except (ValueError, TypeError):
                continue
            created = self._ms(d.get("createdAt"))
            last = self._ms(d.get("lastUpdatedAt")) or created
            if last is not None and last < since:
                continue
            mc = d.get("modelConfig") or {}
            model = mc.get("modelName")
            if model in (None, "", "default"):
                model = d.get("unifiedMode") or "default"
            headers = d.get("fullConversationHeadersOnly")
            messages = len(headers) if isinstance(headers, list) else None
            status = d.get("status")
            out.append({
                "id": d.get("composerId"),
                "source": self.name,
                "runtime": self.name,
                "model": model,
                "started_at": created,
                "last_at": last,
                "ended_at": None,
                "messages": messages,
                "tokens": None,  # usage blobs are empty — metadata only
                "cwd": self._cwd(d),
                "title": d.get("name"),
                "status": status,
                "active": status not in ("completed", "aborted")
                and last is not None and last > t - self.active_window,
            })
        out.sort(key=lambda r: (r.get("last_at") or 0), reverse=True)
        return out[:50]


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

    # -- processes ----------------------------------------------------
    def _processes(self, limit=10):
        """Top CPU/memory consumers — metadata only, no command arguments.

        Uses ``comm`` (executable name only), never the full argv, so process
        secrets in command-line flags never enter the payload. Returns the
        union of the top-``limit`` by CPU and the top-``limit`` by resident
        memory, so both the CPU tile and the Memory tile have something to show.
        """
        fmt = "pid=,pcpu=,rss=,comm="
        if sys.platform == "darwin":
            raw = self._run(["ps", "-Aco", fmt])  # -c → executable name, no args
        elif sys.platform.startswith("linux"):
            raw = self._run(["ps", "-eo", fmt])   # comm is already args-free
        else:
            return None
        if not raw:
            return None
        procs = []
        for line in raw.splitlines():
            parts = line.split(None, 3)  # keep names with spaces intact
            if len(parts) < 4:
                continue
            pid_s, cpu_s, rss_s, name = parts
            try:
                pid = int(pid_s)
                cpu = float(cpu_s)
                rss = int(rss_s) * 1024  # ps reports RSS in KiB
            except ValueError:
                continue
            if pid == 0:
                continue
            procs.append({"pid": pid, "name": name.strip(),
                          "cpu_pct": round(cpu, 1), "rss": rss})
        if not procs:
            return None
        top_cpu = sorted(procs, key=lambda p: p["cpu_pct"], reverse=True)[:limit]
        top_mem = sorted(procs, key=lambda p: p["rss"], reverse=True)[:limit]
        seen, union = set(), []
        for p in top_cpu + top_mem:
            if p["pid"] not in seen:
                seen.add(p["pid"])
                union.append(p)
        return union

    def meter(self):
        return {
            "kind": "system",
            "source": self.name,
            "cpu": self._cpu(),
            "memory": self._memory(),
            "temperature_c": self._temperature(),
            "processes": self._processes(),
        }


REGISTRY = {
    "usage": UsageSource,
    "claude_memory": ClaudeMemorySource,
    "claude_sessions": ClaudeSessionSource,
    "cursor": CursorSource,
    "system": SystemSource,
    "hermes": HermesSource,
    "antigravity": AntigravitySource,
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
