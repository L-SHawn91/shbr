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
```

Add `--json` to any command for machine-readable output.

## Sources

Out of the box `shbr` auto-discovers only generic, public sources:

| source          | provides                        | activation                          |
|-----------------|---------------------------------|-------------------------------------|
| `agentcat`      | per-provider token & quota usage | used automatically if `agentcat` is on `PATH` |
| `claude_memory` | Claude Code memory-file ops      | on by default                       |

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
