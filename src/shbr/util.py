"""Small formatting / environment helpers with no external dependencies."""
from __future__ import annotations

import os
import time


def now() -> float:
    return time.time()


def fmt_tok(n) -> str:
    if not n:
        return "0"
    n = int(n)
    for unit, div in (("B", 1_000_000_000), ("M", 1_000_000), ("K", 1_000)):
        if n >= div:
            return f"{n/div:.1f}{unit}"
    return str(n)


def fmt_bytes(n) -> str:
    if not n:
        return "0"
    n = float(n)
    for unit, div in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if n >= div:
            return f"{n/div:.2f}{unit}"
    return f"{int(n)}B"


def age(ts: float, ref: float | None = None) -> str:
    d = (ref if ref is not None else now()) - ts
    if d < 3600:
        return f"{int(d/60)}m"
    if d < 86400:
        return f"{int(d/3600)}h"
    return f"{int(d/86400)}d"


def which(cmd: str) -> bool:
    return any(
        os.access(os.path.join(p, cmd), os.X_OK)
        for p in os.environ.get("PATH", "").split(os.pathsep)
        if p
    )
