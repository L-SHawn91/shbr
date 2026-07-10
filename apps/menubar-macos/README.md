# SHawn Brain — macOS menu-bar app

The native menu-bar frontend for [`shbr`](../../README.md). A self-contained,
menu-bar-resident SwiftUI app — **no SwiftBar, no host app**. It draws its own
menu-bar item via `MenuBarExtra` and renders the read-only `shbr` core.

- Menu-bar line: the always-on glance — `🧠 15% · 41° · 54%` (CPU · temp · RAM),
  with a 🟡/🔴 marker when the host crosses warn/crit thresholds.
- Dropdown panel: host resources (CPU / memory / temp with bars), per-provider
  agent usage + remaining-quota %, and the recent/active session list.
- Refresh interval picker (2s / 5s / 10s / 30s), persisted across launches.

Read-only, top to bottom: every value comes straight from `shbr menubar --json`.
The app never writes or mutates anything.

## Build & run

```bash
swift build -c release
.build/release/SHawnBrain
```

A menu-bar item appears; there is no dock icon (the app runs as an
`.accessory`). Quit from the panel's **Quit** button.

Requires `shbr` on your `PATH` (the app runs `shbr menubar --json` via a login
shell). Verify with `shbr menubar --json` first; if that errors, the panel shows
the message instead of data.

## Architecture

This is the **product frontend** — a separate Swift package that consumes the
JSON contract. The Python `shbr` core stays a headless, stdlib-only, read-only
engine; this app is a thin renderer on top of it.

- **Phase A (now):** shells out to an installed `shbr menubar --json`.
- **Phase B (distribution):** bundle a frozen `shbr` binary so the `.app` is a
  single download with no Python/`shbr` prerequisite.

| file | role |
|------|------|
| `Sources/SHawnBrain/Snapshot.swift`     | Codable mirror of `shbr menubar --json` (the contract) |
| `Sources/SHawnBrain/BrainModel.swift`   | runs `shbr`, decodes, refreshes on a timer; formatting helpers |
| `Sources/SHawnBrain/SHawnBrainApp.swift`| `@main` `MenuBarExtra` scene + accessory activation policy |
| `Sources/SHawnBrain/BrainFrames.swift`  | G1 Route A menubar frames (idle + peak glow × intensity, base64) |
| `Sources/SHawnBrain/ContentView.swift`  | the dropdown panel |

Regenerate menubar frames after SHawn-slide lock PNG changes:

```bash
python3 scripts/render_brain_frames.py --write-swift
```

The Codable models and `engine.menubar_data()` are two ends of one contract —
change one, change the other.
