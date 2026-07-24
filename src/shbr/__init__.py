"""AI Usage Indicator — local-first usage observability for AI tools.

The ``shbr`` module and command remain compatibility aliases. This read-only
observer watches on-disk agent state — token /
quota usage, persistent-memory operations, and sessions — and never intervenes.

Design contract for this package:
  * The core carries NO deployment-specific paths. Everything private lives in
    config + pluggable source adapters (see ``config.example.toml``).
  * Default config auto-discovers only generic, public sources (on-disk token
    counters for local agents, Claude Code memory files). Vendor-specific
    runtimes are opt-in.
  * Output is metadata only — never prompt or memory content.
"""

APP_NAME = "AI Usage Indicator"
CLI_NAME = "ai-usage-indicator"
__version__ = "0.1.0"
