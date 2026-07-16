"""Source-agnostic composition + rendering.

The engine asks every configured Source for whatever it can provide and merges
the results. It knows nothing about any specific runtime.
"""
from __future__ import annotations

import concurrent.futures
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


def _hide_providers(meters: list, hidden) -> list:
    """Drop user-hidden provider rows from every providers meter, in place.

    ``hidden`` is a set of provider display-names (``[providers] hidden`` in the
    config). This is the display filter: it removes hidden rows *after* they are
    built, so a single mechanism covers both local usage-reader providers and
    connector-fed ones — they share the ``providers`` dict keyed by name.
    """
    hidden = set(hidden or ())
    if not hidden:
        return meters
    for m in meters:
        if m.get("kind") == "providers":
            provs = m.get("providers")
            if provs:
                for name in [n for n in provs if n in hidden]:
                    del provs[name]
    return meters


def _fetch_connector_results(connectors, cache=None, hidden=()) -> list:
    """Run every opt-in connector's network fetch and return the ordered result
    list. No ``meters`` needed — so this can run concurrently with
    ``build_meter`` (see ``build_snapshot``). The three phases (cache-hit
    resolution → parallel fetch → cache put / stale fallback) and their ordering
    guarantees are documented on ``apply_connectors``.
    """
    hidden = set(hidden or ())
    if not connectors:
        return []

    # Phase 1 (sequential, no network): resolve the hidden filter and cache hits,
    # and collect the connectors that still need a live fetch. Each connector
    # keeps an ordered slot so the merged result order is identical to the old
    # sequential loop.  slot = ["hit", result] | ["fetch", None, name]
    slots: list = []
    to_fetch: list = []
    for c in connectors:
        name = getattr(c, "name", None)
        if name and name in hidden:
            continue  # user hid this provider — do not touch the network.
        if cache is not None and name:
            hit = cache.fresh(name)
            if hit is not None:
                slots.append(["hit", hit])
                continue
        slots.append(["fetch", None, name])  # r filled in after the parallel fetch
        to_fetch.append((len(slots) - 1, c))

    # Phase 2 (parallel): every connector needing the network fetches at once, so
    # wall time collapses from sum-of-all connectors to the slowest single one —
    # keeps the on-demand panel-open poll snappy. Fail-silent per connector: an
    # exception (or timeout) becomes None and falls back to cache in phase 3.
    if to_fetch:
        def _fetch(c):
            try:
                return c.fetch()
            except Exception:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(to_fetch), 8)) as ex:
            fetched = list(ex.map(lambda item: _fetch(item[1]), to_fetch))
        for (idx, _c), r in zip(to_fetch, fetched):
            slots[idx][1] = r

    # Phase 3 (sequential, in original order): apply cache put / stale fallback.
    # Cache writes stay single-threaded here so the parallel fetch never races on
    # the cache.
    results = []
    for slot in slots:
        if slot[0] == "hit":
            results.append(slot[1])
            continue
        _, r, name = slot
        if r and r.get("name"):
            if cache is not None and name:
                cache.put(name, r)
            results.append(r)
        elif cache is not None and name:
            stale = cache.stale(name)
            if stale:
                results.append(stale)
    return results


def _merge_connector_results(meters: list, results: list, hidden=()) -> list:
    """Merge fetched connector results into the providers meter, in place.

    The pure-CPU tail of ``apply_connectors``: no network, no ``cache`` — it only
    folds the already-fetched ``results`` (from ``_fetch_connector_results``) into
    ``providers[name]["quotas"]`` and drops hidden rows. Split out so the fetch can
    overlap ``build_meter`` and only this cheap merge needs ``meters``.
    """
    hidden = set(hidden or ())
    if not results:
        return _hide_providers(meters, hidden)
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
    return _hide_providers(meters, hidden)


def apply_connectors(meters: list, connectors, cache=None, hidden=()) -> list:
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

    ``hidden`` (provider display-names) is the single choke point for the
    user's on/off choice: a hidden connector is skipped *before* its network
    fetch, and hidden rows are dropped from the providers meter on every return
    path — so hiding a name suppresses both its local-usage row and its
    connector fetch at once.

    Serial convenience wrapper (fetch → merge) kept for the ``cli`` cmd_meter
    path; ``build_snapshot`` calls the two halves separately so the fetch can
    overlap the host meter.
    """
    hidden = set(hidden or ())
    if not connectors:
        return _hide_providers(meters, hidden)
    results = _fetch_connector_results(connectors, cache, hidden)
    return _merge_connector_results(meters, results, hidden)


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
        for s in _diverse_sessions(sessions, 12)
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
def _session_recency(s: dict) -> float:
    """Last-seen timestamp across heterogeneous sources.

    Hermes rows carry only started_at/ended_at; the native claude/cursor readers
    carry a precise last_at. Ordering by whichever is freshest lets a genuinely
    active session bubble up regardless of which runtime produced it.
    """
    return s.get("last_at") or s.get("ended_at") or s.get("started_at") or 0


def build_sessions(sources, hours: float) -> dict:
    out = []
    for s in sources:
        for sess in s.sessions(hours):
            out.append({**sess, "source": sess.get("source", s.name)})
    out.sort(key=_session_recency, reverse=True)
    return {"hours": hours, "sessions": out[:50]}


def _diverse_sessions(sessions: list, limit: int) -> list:
    """Round-robin across sources so no single runtime crowds out the rest.

    HermesSource can return dozens of open sessions; a plain head-slice would
    bury the (fewer) claude/cursor rows entirely — the "왜 죄다 헤르메스" bug.
    Within each source the input order (recency-sorted) is preserved, so each
    round still surfaces that source's freshest session first.
    """
    groups: dict = {}
    for s in sessions:
        groups.setdefault(s.get("source"), []).append(s)
    order = sorted(groups, key=lambda k: _session_recency(groups[k][0]), reverse=True)
    out: list = []
    while len(out) < limit and any(groups[k] for k in order):
        for k in order:
            if groups[k]:
                out.append(groups[k].pop(0))
                if len(out) >= limit:
                    break
    return out


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
def meters_with_connectors(sources, connectors=(), cache=None, hidden=()):
    """Build the host meters and fold in connector quota rows, overlapping the two.

    The host meter (top -l 2, ~1.5s) and the connector network fetch (~1s) are
    independent until the final merge, so we run the fetch on a worker while
    build_meter runs here, then fold the results in. Every poll pays
    max(meter, fetch) instead of their sum. Both build_snapshot and the menu-bar
    payload go through here so the overlap is applied on every poll path.
    """
    hidden = set(hidden or ())
    if connectors:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_fetch_connector_results, connectors, cache, hidden)
            meters = build_meter(sources)
            results = fut.result()
        return _merge_connector_results(meters, results, hidden)
    return _hide_providers(build_meter(sources), hidden)


def build_snapshot(sources, connectors=(), cache=None, hidden=()) -> dict:
    meters = meters_with_connectors(sources, connectors, cache, hidden)
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
