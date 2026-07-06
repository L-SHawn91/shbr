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


def _render_providers(m: list, lines: list) -> None:
    src = m.get("source", "providers")
    lines.append(f"[meter · {src} — CLI providers]")
    provs = m.get("providers") or {}
    if not provs:
        lines.append("  (no providers reported)")
    for name, p in provs.items():
        quota_bits = []
        for q in p.get("quotas") or []:
            rp = q.get("remainingPercent")
            if rp is not None:
                quota_bits.append(f"{q.get('window') or q.get('id')}:{rp:.0f}%left")
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


def render_meter(meters: list) -> list:
    lines: list = []
    if not meters:
        return ["[meter] no usage sources available"]
    for m in meters:
        if m.get("kind") == "providers":
            _render_providers(m, lines)
        elif m.get("kind") == "aggregate":
            _render_aggregate(m, lines)
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
def build_snapshot(sources) -> dict:
    meters = build_meter(sources)
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
