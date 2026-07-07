"""Configuration loading.

Resolution order:
  1. explicit path passed to ``load()``
  2. ``$SHBR_CONFIG``
  3. ``~/.config/shbr/config.toml``
  4. built-in defaults (generic, public sources only)

A config file is *merged over* the defaults at the source level, so a private
deployment only needs to add/enable the extra sources it wants without
re-listing the generic ones.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

DEFAULT_STATE_DIR = "~/.local/state/shbr"
DEFAULT_CONFIG_PATH = "~/.config/shbr/config.toml"
CLAUDE_MEMORY_GLOB = "~/.claude/projects/*/memory/*.md"

# Generic, ships-to-anyone defaults. No vendor-private runtime paths here.
DEFAULTS = {
    "sources": {
        "usage": {"enabled": True},  # native local token reader (codex/claude on-disk state)
        "claude_memory": {"enabled": True, "glob": CLAUDE_MEMORY_GLOB},
        "claude_sessions": {"enabled": True},  # Claude Code transcripts → per-session rows (available() gates)
        "cursor": {"enabled": True},  # Cursor IDE composer sessions (available() gates when not installed)
        "system": {"enabled": True},  # host CPU / memory / temperature observer
    },
}


def _expand(p) -> Path:
    return Path(os.path.expanduser(str(p)))


class Config:
    def __init__(self, data: dict, source_path: Path | None = None):
        self.path = source_path
        self.state_dir = _expand(data.get("state_dir", DEFAULT_STATE_DIR))
        mf = data.get("migrate_from")
        self.migrate_from = _expand(mf) if mf else None
        self.sources: dict = data.get("sources", {})

    def source(self, name: str) -> dict:
        return self.sources.get(name, {}) or {}

    def enabled(self, name: str) -> bool:
        return bool(self.source(name).get("enabled"))


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if k == "sources" and isinstance(v, dict):
            merged = {n: dict(cfg) for n, cfg in base.get("sources", {}).items()}
            for n, cfg in v.items():
                merged[n] = {**merged.get(n, {}), **(cfg or {})}
            out["sources"] = merged
        else:
            out[k] = v
    return out


def load(explicit: str | None = None) -> Config:
    path = explicit or os.environ.get("SHBR_CONFIG") or DEFAULT_CONFIG_PATH
    p = _expand(path)
    if p.exists():
        with p.open("rb") as fh:
            file_data = tomllib.load(fh)
        return Config(_merge(DEFAULTS, file_data), p)
    return Config(dict(DEFAULTS), None)
