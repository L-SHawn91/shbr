# shbr — SHawn Brain

**Local-first, read-only observability for the CLI AI agents already running on your machine.**

`shbr` is a thin observer. It does not run your agents, proxy your traffic, or
touch a live database. It reads what your agents already write to disk — usage
snapshots, persistent-memory files, session records — and gives you one place to
see token/quota burn, memory-write activity, and recent sessions across tools.

- **Zero instrumentation.** No SDK to import, no wrapper to launch your agent
  through, no API keys handed over. If a tool already writes state locally,
  `shbr` reads it.
- **Read-only by contract.** Every source is opened read-only (SQLite via
  `mode=ro`, files via stat/glob). `shbr` never writes to a source, only to its
  own append-only event log under `~/.local/state/shbr`.
- **Metadata only.** Token counts, byte deltas, timestamps, model names. Never
  prompt or completion content.
- **Config-driven sources.** Support for a new agent runtime is a small adapter
  plus a few lines of TOML — no core changes.

## Install

```bash
pip install shbr        # once published
# or, from a checkout:
pip install -e .
```

## Use

```bash
shbr snapshot      # everything: usage + memory ops + recent sessions
shbr meter         # token / quota usage per source
shbr memory        # persistent-memory file operations since last scan
shbr sessions      # recent + active sessions
shbr history -n 30 # recent recorded events
shbr config        # show resolved config + which sources are active
shbr menubar       # SwiftBar/xbar plugin output (glance line + dropdown)
```

Add `--json` to any command for machine-readable output.

## Menu bar (macOS)

The always-on glance — `🧠 9% · 39° · 53%` (CPU · temp · RAM) — plus a dropdown
with the full meter, per-agent usage/quota, and recent sessions. `shbr` itself
stays a headless, read-only CLI: it only *emits* the data; a separate frontend
draws the menu bar.

**SHawn Brain app (recommended).** A self-contained native menu-bar app — no
third-party host. It shells out to `shbr menubar --json` and renders the panel
itself.

```bash
cd apps/menubar-macos
swift build -c release
.build/release/SHawnBrain          # menu-bar item appears; no dock icon
```

Requires `shbr` on your `PATH`. See [`apps/menubar-macos/README.md`](apps/menubar-macos/README.md)
for the refresh-interval control and packaging notes.

**SwiftBar plugin (dev scaffold).** `shbr menubar` (no `--json`) also prints
[SwiftBar](https://swiftbar.app)/xbar plugin text, handy for a quick check
without building the app:

```bash
brew install swiftbar
mkdir -p ~/.config/swiftbar-plugins
cp contrib/swiftbar/shbr.10s.sh ~/.config/swiftbar-plugins/   # or symlink
```

The filename sets the refresh interval (`shbr.10s.sh` = every 10s). For a
snappier pure-resource meter, rename to `shbr.3s.sh` and use `shbr menubar
--no-agents` (skips the agent-usage query — no subprocess/network call).

## Sources

Out of the box `shbr` auto-discovers only generic, public sources:

| source          | provides                        | activation                          |
|-----------------|---------------------------------|-------------------------------------|
| `usage`         | per-agent token usage, read straight from each local agent's on-disk ledger | on by default; screens all known agents, shows only the active ones |
| `claude_memory` | Claude Code memory-file ops      | on by default                       |
| `system`        | host CPU / memory / temperature  | on by default (stdlib + OS utilities) |

Everything else is opt-in. See [`config.example.toml`](config.example.toml) for
how to point `shbr` at additional runtimes (the shipped example includes a
commented `hermes` adapter for a local SQLite-backed agent).

## Configuration

`shbr` looks for config in this order: `--config <path>` → `$SHBR_CONFIG` →
`~/.config/shbr/config.toml` → built-in defaults. Your config is merged *over*
the defaults per source, so you only declare what you want to add or change.

## Status

Phase 1 — meter / memory / sessions / snapshot. `registry`, `drift`, and `guard`
subcommands are stubbed for later phases.

## License

Apache-2.0.
