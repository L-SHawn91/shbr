"""Command-line entry point."""
from __future__ import annotations

import argparse
import json
import re

from . import APP_NAME, __version__
from . import config, engine
from .cache import ConnectorCache
from .connectors import build_connectors
from .sources import build_sources
from .state import State


class Ctx:
    def __init__(self, config_path: str | None = None):
        self.cfg = config.load(config_path)
        self.state = State(self.cfg)
        self.sources = build_sources(self.cfg)
        # Opt-in network quota readers; empty unless a connector is enabled.
        self.connectors = build_connectors(self.cfg)
        # Short-TTL disk cache so a tight refresh reads last quota, not the wire.
        self.cache = ConnectorCache(self.cfg.state_dir)

    def source_names(self) -> list:
        return [s.name for s in self.sources]


def _emit(lines):
    print("\n".join(lines))


# --------------------------------------------------------------- commands
def cmd_snapshot(args, ctx: Ctx):
    snap = engine.build_snapshot(ctx.sources, ctx.connectors, ctx.cache,
                                 ctx.cfg.hidden_set())
    prev = ctx.state.load_index()
    ops = engine.diff_memory(prev, snap["_inv"])
    if not args.no_update:
        ctx.state.save_index(snap["_inv"])
        for o in ops:
            ctx.state.append_event({"kind": "memory", **o})
        sess = snap.get("sessions") or []
        ctx.state.append_event({"kind": "usage", "sessions": len(sess),
                                "sources": ctx.source_names()})
    if args.json:
        snap.pop("_inv", None)
        print(json.dumps({**snap, "memory_ops": ops}, indent=2))
    else:
        _emit(engine.render_snapshot(snap, ops))


def cmd_meter(args, ctx: Ctx):
    meters = engine.apply_connectors(engine.build_meter(ctx.sources),
                                     ctx.connectors, ctx.cache,
                                     ctx.cfg.hidden_set())
    if args.json:
        print(json.dumps(meters, indent=2))
    else:
        _emit(engine.render_meter(meters))


def cmd_resources(args, ctx: Ctx):
    meters = [m for m in engine.build_meter(ctx.sources) if m.get("kind") == "system"]
    if args.json:
        print(json.dumps(meters, indent=2))
    elif meters:
        _emit(engine.render_meter(meters))
    else:
        _emit(["[resources] system source not available"])


def cmd_menubar(args, ctx: Ctx):
    if args.no_agents:
        meters = engine.build_meter(ctx.sources)
        meters = [m for m in meters if m.get("kind") == "system"]
    else:
        # Overlap the host meter with the connector fetch (see
        # engine.meters_with_connectors) so the panel-open poll pays
        # max(meter, fetch) instead of their sum.
        meters = engine.meters_with_connectors(ctx.sources, ctx.connectors,
                                               ctx.cache, ctx.cfg.hidden_set())
    sess = engine.build_sessions(ctx.sources, args.hours)["sessions"]
    if args.json:
        mem_inv = engine.scan_memory(ctx.sources)
        print(json.dumps(engine.menubar_data(meters, sess, mem_inv), indent=2))
    else:
        _emit(engine.render_menubar(meters, sess))


def cmd_memory(args, ctx: Ctx):
    inv = engine.scan_memory(ctx.sources)
    prev = ctx.state.load_index()
    ops = engine.diff_memory(prev, inv)
    if not args.no_update:
        ctx.state.save_index(inv)
        for o in ops:
            ctx.state.append_event({"kind": "memory", **o})
    if args.json:
        print(json.dumps({"memory": engine.memory_summary(inv),
                          "memory_ops": ops}, indent=2))
    else:
        _emit(engine.render_memory(inv, ops))


def cmd_sessions(args, ctx: Ctx):
    data = engine.build_sessions(ctx.sources, args.hours)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        _emit(engine.render_sessions(data))


def cmd_history(args, ctx: Ctx):
    ctx.state.ensure()
    if not ctx.state.events.exists():
        print("(no events recorded yet)")
        return
    lines = ctx.state.events.read_text().splitlines()[-args.n:]
    for ln in lines:
        print(ln)


def cmd_config(args, ctx: Ctx):
    print(f"config: {ctx.cfg.path or '(built-in defaults)'}")
    print(f"state_dir: {ctx.cfg.state_dir}")
    print(f"active sources: {', '.join(ctx.source_names()) or '(none)'}")


# --------------------------------------------------------- providers on/off
def _provider_rows(cfg) -> list:
    """Every known provider display-row with tier + current show/hide state.

    Merges the two layers by display-name: local usage-readers
    (``USAGE_PROVIDER_NAMES``) and network connectors (``CONNECTOR_REGISTRY``,
    keyed by ``cls.name`` which can equal a usage-reader name — e.g. ``claude``
    has both a local ledger and a live-quota connector, so it is one row fed by
    both). ``tier`` reflects the connector tier when present, else ``local``.
    """
    from .connectors import CONNECTOR_REGISTRY
    from .sources import USAGE_PROVIDER_NAMES

    hidden = cfg.hidden_set()
    rows: dict = {}

    def row(name: str) -> dict:
        return rows.setdefault(name, {
            "name": name, "layers": [], "tier": "local",
            "enabled": False, "hidden": name in hidden,
        })

    usage_on = cfg.enabled("usage")
    for n in USAGE_PROVIDER_NAMES:
        r = row(n)
        r["layers"].append("usage")
        if usage_on:
            r["enabled"] = True
    for key, cls in CONNECTOR_REGISTRY.items():
        name = getattr(cls, "name", key)
        r = row(name)
        r["layers"].append("connector")
        r["tier"] = getattr(cls, "tier", "gray")
        if cfg.source(key).get("enabled"):
            r["enabled"] = True
    return list(rows.values())


def _fmt_hidden_array(names) -> str:
    if not names:
        return "hidden = []"
    return "hidden = [" + ", ".join('"' + n + '"' for n in names) + "]"


def _persist_hidden(cfg, names) -> "Path":
    """Rewrite ``[providers] hidden`` in place, preserving all other text.

    STDLIB-only surgical edit — ``tomllib`` has no writer, so the file's
    explanatory comments and every other stanza must survive untouched. Always
    writes a single-line array; the ``[providers]`` table is created on first
    use.
    """
    path = cfg.path or config._expand(config.DEFAULT_CONFIG_PATH)
    text = path.read_text() if path.exists() else ""
    line = _fmt_hidden_array(sorted(names))
    hdr = re.search(r"(?m)^\[providers\][^\n]*\n", text)
    if hdr:
        # Section body spans from just after the header to the next table
        # header (``[...]`` at line start) or EOF.
        rest = text[hdr.end():]
        nxt = re.search(r"(?m)^\[", rest)
        end = hdr.end() + (nxt.start() if nxt else len(rest))
        body = text[hdr.end():end]
        new_body, n = re.subn(
            r"(?m)^[ \t]*hidden[ \t]*=[ \t]*\[[^\]]*\][^\n]*\n?",
            line + "\n", body, count=1)
        if n == 0:
            new_body = line + "\n" + body
        new = text[:hdr.end()] + new_body + text[end:]
    else:
        prefix = text
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix:
            prefix += "\n"
        new = prefix + "[providers]\n" + line + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return path


def cmd_providers(args, ctx: Ctx):
    action = getattr(args, "action", "list") or "list"
    rows = _provider_rows(ctx.cfg)
    known = {r["name"] for r in rows}

    if action in ("show", "hide"):
        name = getattr(args, "name", None)
        if not name:
            print(f"usage: shbr providers {action} <name>")
            return 2
        if name not in known:
            print(f"unknown provider: {name}")
            print(f"known: {', '.join(sorted(known))}")
            return 2
        hidden = ctx.cfg.hidden_set()
        if action == "hide":
            hidden.add(name)
        else:
            hidden.discard(name)
        path = _persist_hidden(ctx.cfg, hidden)
        state = "hidden" if action == "hide" else "shown"
        print(f"{name}: {state}  ({path})")
        return 0

    if args.json:
        print(json.dumps({"providers": rows}, indent=2))
        return 0
    lines = []
    for r in rows:
        mark = "hidden" if r["hidden"] else "shown"
        layers = "+".join(r["layers"])
        lines.append(
            f"{r['name']:<11} {mark:<6} tier={r['tier']:<8} "
            f"{'on' if r['enabled'] else 'off':<3} [{layers}]")
    _emit(lines or ["(no providers)"])
    return 0


def _stub(phase: str, note: str):
    def run(args, ctx: Ctx):
        print(f"[{phase}] not yet implemented — {note}")
    return run


# ------------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="shbr",
        description=f"{APP_NAME} — read-only observability for CLI AI agents",
    )
    ap.add_argument("--version", action="version",
                    version=f"{APP_NAME} {__version__}")
    ap.add_argument("--config", help="path to a config.toml (overrides $SHBR_CONFIG)")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("snapshot", help="full read-only snapshot")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-update", action="store_true",
                   help="do not persist the diff baseline / events")
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("meter", help="token / quota usage")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_meter)

    p = sub.add_parser("resources", help="host CPU / memory / temperature")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_resources)

    p = sub.add_parser("menubar", help="menu-bar payload: SwiftBar/xbar text, or "
                                       "--json for a native menu-bar app")
    p.add_argument("--json", action="store_true",
                   help="structured payload (glance + meters + sessions) for the "
                        "native SHawn Brain menu-bar app to render")
    p.add_argument("--no-agents", action="store_true",
                   help="system resources only — skip agent usage sources (fast, "
                        "safe for a tight refresh interval)")
    p.add_argument("--hours", type=float, default=24.0)
    p.set_defaults(fn=cmd_menubar)

    p = sub.add_parser("memory", help="persistent-memory operations")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-update", action="store_true")
    p.set_defaults(fn=cmd_memory)

    p = sub.add_parser("sessions", help="recent / active sessions")
    p.add_argument("--json", action="store_true")
    p.add_argument("--hours", type=float, default=24.0)
    p.set_defaults(fn=cmd_sessions)

    p = sub.add_parser("history", help="recent recorded events")
    p.add_argument("-n", type=int, default=20)
    p.set_defaults(fn=cmd_history)

    p = sub.add_parser("config", help="show resolved config + active sources")
    p.set_defaults(fn=cmd_config)

    p = sub.add_parser("providers",
                       help="list AI providers; show/hide them in the meter")
    p.add_argument("action", nargs="?", choices=["list", "show", "hide"],
                   default="list",
                   help="list (default), or show/hide a provider by name")
    p.add_argument("name", nargs="?",
                   help="provider display-name for show/hide (e.g. gemini, cursor)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_providers)

    for name, note in (
        ("registry", "cross-agent registry view (Phase 2)"),
        ("drift", "instruction/config drift detection (Phase 3)"),
        ("guard", "risk-gate advisories (Phase 4)"),
    ):
        p = sub.add_parser(name, help=note)
        p.set_defaults(fn=_stub(name, note))

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 0
    ctx = Ctx(args.config)
    return args.fn(args, ctx) or 0
