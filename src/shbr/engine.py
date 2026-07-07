"""Source-agnostic composition + rendering.

The engine asks every configured Source for whatever it can provide and merges
the results. It knows nothing about any specific runtime.
"""
from __future__ import annotations

import glob
import os

from . import APP_NAME
from .util import age, fmt_bytes, fmt_tok, now

# ------------------------------------------------------------------ meter
def build_meter(sources) -> list:
    out = []
    for s in sources:
        m = s.meter()
        if m is not None:
            out.append(m)
    return out


def apply_connectors(meters: list, connectors, cache=None) -> list:
    """Merge opt-in connectors' live quota into the providers meter, in place.

    A connector returns the remaining-quota a local file cannot hold (see
    ``connectors.py``). Each result is ``{"name", "quotas", ...}``; its quotas
    land in ``providers[name]["quotas"]`` so they flow through the same render
    path (text + JSON) as any locally-read provider. If no providers meter
    exists yet (e.g. the local usage source is off), one is synthesised so the
    live quota still shows. Fail-silent: a dead connector is a non-event.

    ``cache`` (a ``cache.ConnectorCache``, optional) short-circuits the network:
    a result younger than the TTL is served from disk without a fetch, a fresh
    fetch is written back, and a failed fetch falls back to the last cached value
    so the menu bar holds its last-known quota instead of blanking.
    """
    if not connectors:
        return meters
    results = []
    for c in connectors:
        name = getattr(c, "name", None)
        if cache is not None and name:
            hit = cache.fresh(name)
            if hit is not None:
                results.append(hit)
                continue
        try:
            r = c.fetch()
        except Exception:
            r = None
        if r and r.get("name"):
            if cache is not None and name:
                cache.put(name, r)
            results.append(r)
        elif cache is not None and name:
            stale = cache.stale(name)
            if stale:
                results.append(stale)
    if not results:
        return meters
    provm = next((m for m in meters if m.get("kind") == "providers"), None)
    if provm is None:
        provm = {"kind": "providers", "source": "connectors",
                 "providers": {}, "memory_bytes": {}, "process_count": None}
        meters.append(provm)
    provs = provm.setdefault("providers", {})
    for r in results:
        p = provs.setdefault(r["name"], {"status": None, "today": None,
                                         "week": None, "month": None,
                                         "all": None, "quotas": []})
        p.setdefault("quotas", []).extend(r.get("quotas") or [])
        if r.get("status") and not p.get("status"):
            p["status"] = r["status"]
    return meters


def _render_providers(m: list, lines: list) -> None:
    src = m.get("source", "providers")
    lines.append(f"[meter · {src} — CLI providers]")
    provs = m.get("providers") or {}
    if not provs:
        lines.append("  (no providers reported)")
    for name, p in provs.items():
        # Lowest-remaining first — that's the actionable end (nearest a limit).
        # Some providers (gemini) enumerate a bucket per model; cap the text
        # render so the line stays glanceable. --json keeps every quota.
        ranked = sorted(
            ((q.get("remainingPercent"), q.get("window") or q.get("id"))
             for q in p.get("quotas") or []
             if q.get("remainingPercent") is not None),
            key=lambda t: t[0],
        )
        cap = 4
        quota_bits = [f"{w}:{rp:.0f}%left" for rp, w in ranked[:cap]]
        if len(ranked) > cap:
            quota_bits.append(f"+{len(ranked) - cap} more")
        quota = "  quota[" + ", ".join(quota_bits) + "]" if quota_bits else ""
        lines.append(
            f"  {name:10s} today {fmt_tok(p.get('today')):>7} / "
            f"week {fmt_tok(p.get('week')):>7} / month {fmt_tok(p.get('month')):>7}"
            f"  [{p.get('status') or '?'}]{quota}"
        )
    mem = m.get("memory_bytes") or {}
    if mem:
        bits = "  ".join(f"{k}:{fmt_bytes(v)}" for k, v in mem.items())
        pc = m.get("process_count")
        lines.append(f"  RAM(RSS): {bits}" + (f"   procs={pc}" if pc else ""))


def _render_aggregate(m: list, lines: list) -> None:
    src = m.get("source", "aggregate")
    lines.append(f"[meter · {src} — aggregate]")
    lines.append(
        f"  {m.get('sessions', 0)} sessions   "
        f"tokens today {fmt_tok(m.get('today'))} / week {fmt_tok(m.get('week'))}   "
        f"(in {fmt_tok(m.get('input'))}, out {fmt_tok(m.get('output'))}, "
        f"cacheR {fmt_tok(m.get('cache_read'))})"
    )
    cost = m.get("actual_cost_usd") or 0
    cs = m.get("cost_status") or {}
    cs_bits = ", ".join(f"{k}:{v}" for k, v in cs.items())
    lines.append(f"  cost: ${cost:.2f} actual   cost_status[{cs_bits}]")
    bm = m.get("by_model") or []
    if bm:
        top = ", ".join(f"{x['model']}({fmt_tok(x['tokens'])})" for x in bm)
        lines.append(f"  top models: {top}")


def _render_system(m: list, lines: list) -> None:
    src = m.get("source", "system")
    lines.append(f"[meter · {src} — host resources]")
    cpu = m.get("cpu") or {}
    util = cpu.get("util_pct")
    util_s = f"{util:.0f}%" if util is not None else "n/a"
    load = [cpu.get("load1"), cpu.get("load5"), cpu.get("load15")]
    load_s = "/".join(f"{x:.2f}" if x is not None else "?" for x in load)
    lines.append(f"  CPU: {util_s} used   load {load_s}   ({cpu.get('ncpu','?')} cores)")
    mem = m.get("memory") or {}
    if mem:
        used, total = mem.get("used"), mem.get("total")
        pct = mem.get("used_pct")
        pct_s = f" ({pct:.0f}%)" if pct is not None else ""
        if used is not None and total:
            lines.append(f"  RAM: {fmt_bytes(used)} / {fmt_bytes(total)} used{pct_s}   "
                         f"avail {fmt_bytes(mem.get('available') or 0)}")
        elif total:
            lines.append(f"  RAM: {fmt_bytes(total)} total")
    temp = m.get("temperature_c")
    lines.append(f"  TEMP: {temp:.1f}°C" if temp is not None else "  TEMP: n/a")


def render_meter(meters: list) -> list:
    lines: list = []
    if not meters:
        return ["[meter] no usage sources available"]
    for m in meters:
        if m.get("kind") == "providers":
            _render_providers(m, lines)
        elif m.get("kind") == "aggregate":
            _render_aggregate(m, lines)
        elif m.get("kind") == "system":
            _render_system(m, lines)
    return lines


# ---------------------------------------------------------------- menubar
# SwiftBar / xbar plugin protocol: the first line(s) render in the menu bar,
# a "---" separator introduces the dropdown, and "key=value" params after a
# trailing " | " style each dropdown row. This gives shbr a RunCat-style
# always-on glance without any GUI code — a host app polls `shbr menubar`.

def _glance(sysm: dict | None) -> dict:
    """Structured menu-bar glance: the few numbers shown always-on.

    alert is a severity level ("crit" / "warn" / None) rather than a colour, so
    each frontend (SwiftBar text, native app) maps it to its own styling.
    """
    if not sysm:
        return {"cpu_pct": None, "temp_c": None, "mem_pct": None, "alert": None}
    cpu = (sysm.get("cpu") or {}).get("util_pct")
    temp = sysm.get("temperature_c")
    mem = (sysm.get("memory") or {}).get("used_pct")
    # temp and cpu share no scale, but either running hot is worth flagging
    alert = None
    if (cpu is not None and cpu >= 90) or (temp is not None and temp >= 90):
        alert = "crit"
    elif (cpu is not None and cpu >= 70) or (temp is not None and temp >= 80):
        alert = "warn"
    return {"cpu_pct": cpu, "temp_c": temp, "mem_pct": mem, "alert": alert}


_ALERT_COLOR = {"crit": "red", "warn": "#e0a000"}


def _menubar_title(sysm: dict | None):
    """(title, alert_color) for the always-visible SwiftBar menu-bar line."""
    g = _glance(sysm)
    if g["cpu_pct"] is None and g["temp_c"] is None and g["mem_pct"] is None:
        return "🧠 shbr", None
    bits = [f"{g['cpu_pct']:.0f}%" if g["cpu_pct"] is not None else "–%"]
    if g["temp_c"] is not None:
        bits.append(f"{g['temp_c']:.0f}°")
    if g["mem_pct"] is not None:
        bits.append(f"{g['mem_pct']:.0f}%")
    return "🧠 " + " · ".join(bits), _ALERT_COLOR.get(g["alert"])


def _memory_block(inv: dict | None) -> dict:
    """Per-source memory metadata for the drill-down view — counts, byte totals,
    and the file list (path/name/size/mtime). Metadata only; no file *content*
    ever leaves the core (the frontend reads the user's own files on demand)."""
    out: dict = {}
    for label, files in (inv or {}).items():
        items = sorted(
            (
                {
                    "path": fp,
                    "name": os.path.basename(fp),
                    "size": meta["size"],
                    "mtime": meta["mtime"],
                }
                for fp, meta in files.items()
            ),
            key=lambda x: x["mtime"],
            reverse=True,
        )
        out[label] = {
            "files": len(files),
            "bytes": sum(f["size"] for f in files.values()),
            "items": items,
        }
    return out


def menubar_data(meters: list, sessions: list, memory_inv: dict | None = None) -> dict:
    """Structured menu-bar payload — the contract a native frontend consumes.

    Same information the SwiftBar text view renders, but as data: a glance line,
    the raw system meter, the agent-usage meters, a trimmed session list, and
    (for the drill-down detail view) per-source memory-file metadata.
    """
    sysm = next((m for m in meters if m.get("kind") == "system"), None)
    agents = [m for m in meters if m.get("kind") != "system"]
    sess = [
        {
            "active": bool(s.get("active")),
            "source": s.get("source"),
            "model": s.get("model"),
            "tokens": s.get("tokens"),
            "cwd": s.get("cwd"),
            "started_at": s.get("started_at"),
        }
        for s in sessions[:8]
    ]
    return {
        "glance": _glance(sysm),
        "system": sysm,
        "agents": agents,
        "sessions": sess,
        "memory": _memory_block(memory_inv),
        "session_count": len(sessions),
        "active_count": sum(1 for s in sessions if s.get("active")),
        "ts": round(now(), 3),
    }


def render_menubar(meters: list, sessions: list) -> list:
    sysm = next((m for m in meters if m.get("kind") == "system"), None)
    title, alert = _menubar_title(sysm)
    lines = [f"{title} | color={alert}" if alert else title, "---"]

    detail = render_meter(meters) or ["(no sources available)"]
    for ln in detail:
        lines.append(f"{ln} | font=Menlo size=12 trim=false")

    active = [s for s in sessions if s.get("active")]
    lines.append("---")
    lines.append(
        f"sessions (24h): {len(sessions)}   active: {len(active)} | font=Menlo size=12"
    )
    for s in sessions[:5]:
        mark = "●" if s.get("active") else "○"
        model = (s.get("model") or "?")[:20]
        cwd = os.path.basename(s.get("cwd") or "") or "-"
        lines.append(
            f"{mark} {s.get('source','?')[:6]:6s} {model:20s} "
            f"{fmt_tok(s.get('tokens')):>7}  {cwd} | font=Menlo size=12 trim=false"
        )
    lines.append("---")
    lines.append("Refresh | refresh=true")
    return lines


# ----------------------------------------------------------------- memory
def scan_memory(sources) -> dict:
    globs: dict = {}
    for s in sources:
        globs.update(s.memory_globs())
    inv: dict = {}
    for label, pattern in globs.items():
        files = {}
        for fp in glob.glob(pattern):
            try:
                st = os.stat(fp)
            except OSError:
                continue
            files[fp] = {"size": st.st_size, "mtime": round(st.st_mtime, 3)}
        inv[label] = files
    return inv


def diff_memory(prev: dict, cur: dict) -> list:
    ops = []
    for label, files in cur.items():
        old = prev.get(label, {})
        for fp, meta in files.items():
            if fp not in old:
                ops.append({"op": "created", "agent": label, "path": fp,
                            "delta_bytes": meta["size"]})
            elif meta["mtime"] != old[fp]["mtime"] or meta["size"] != old[fp]["size"]:
                ops.append({"op": "modified", "agent": label, "path": fp,
                            "delta_bytes": meta["size"] - old[fp]["size"]})
        for fp in old:
            if fp not in files:
                ops.append({"op": "deleted", "agent": label, "path": fp,
                            "delta_bytes": -old[fp]["size"]})
    return ops


def memory_summary(inv: dict) -> dict:
    out = {}
    for label, files in inv.items():
        out[label] = {
            "files": len(files),
            "bytes": sum(f["size"] for f in files.values()),
        }
    return out


def render_memory(inv: dict, ops: list) -> list:
    lines = ["[memory — persistent .md operations]"]
    summ = memory_summary(inv)
    if not summ:
        lines.append("  (no memory sources configured)")
    for label, s in summ.items():
        lines.append(f"  {label:10s} {s['files']:>3} files   {fmt_bytes(s['bytes'])}")
    if ops:
        lines.append(f"  changes since last scan: {len(ops)}")
        for o in ops[:12]:
            d = o["delta_bytes"]
            sign = "+" if d >= 0 else ""
            name = os.path.basename(o["path"])
            lines.append(f"    {o['op']:8s} {o['agent']}:{name}  ({sign}{fmt_bytes(abs(d))})")
    else:
        lines.append("  changes since last scan: 0")
    return lines


# --------------------------------------------------------------- sessions
def build_sessions(sources, hours: float) -> dict:
    out = []
    for s in sources:
        for sess in s.sessions(hours):
            out.append({**sess, "source": sess.get("source", s.name)})
    out.sort(key=lambda x: x.get("started_at") or 0, reverse=True)
    return {"hours": hours, "sessions": out[:50]}


def render_sessions(data: dict) -> list:
    sess = data.get("sessions") or []
    lines = [f"[sessions — last {data.get('hours')}h · {len(sess)} shown]"]
    if not sess:
        lines.append("  (no session sources reported activity)")
    for s in sess:
        mark = "●" if s.get("active") else "○"
        started = s.get("started_at")
        ago = age(started) if started else "  ?"
        model = (s.get("model") or "?")[:24]
        cwd = os.path.basename(s.get("cwd") or "") or "-"
        branch = f"@{s['git_branch']}" if s.get("git_branch") else ""
        ho = f" ⇄{s['handoff_state']}" if s.get("handoff_state") else ""
        lines.append(
            f"  {mark} {s.get('source','?')[:6]:6s} {ago:>4} ago  {model:24s} "
            f"{fmt_tok(s.get('tokens')):>7}  {s.get('messages') or 0}msg  {cwd}{branch}{ho}"
        )
    return lines


# --------------------------------------------------------------- snapshot
def build_snapshot(sources, connectors=(), cache=None) -> dict:
    meters = apply_connectors(build_meter(sources), connectors, cache)
    inv = scan_memory(sources)
    sessions = build_sessions(sources, 24.0)
    return {
        "ts": round(now(), 3),
        "meters": meters,
        "memory": memory_summary(inv),
        "sessions": sessions["sessions"],
        "_inv": inv,  # internal, for the caller to persist a diff baseline
    }


def render_snapshot(snap: dict, ops: list) -> list:
    lines = [f"═══ {APP_NAME} — snapshot ═══"]
    lines += render_meter(snap.get("meters") or [])
    lines.append("")
    lines += render_memory(snap.get("_inv") or {}, ops)
    lines.append("")
    lines += render_sessions({"hours": 24.0, "sessions": snap.get("sessions") or []})
    return lines
