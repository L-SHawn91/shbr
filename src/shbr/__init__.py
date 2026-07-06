"""shbr — local-first, zero-instrumentation observability for CLI AI agents.

Codename SHBr. A read-only observer: it watches on-disk agent state — token /
quota usage, persistent-memory operations, and sessions — and never intervenes.

Design contract for this package:
  * The core carries NO deployment-specific paths. Everything private lives in
    config + pluggable source adapters (see ``config.example.toml``).
  * Default config auto-discovers only generic, public sources (AgentCat if it
    is installed, Claude Code memory files). Vendor-specific runtimes are opt-in.
  * Output is metadata only — never prompt or memory content.
"""

APP_NAME = "SHBr"
__version__ = "0.1.0"
