# AI Usage Indicator — macOS menu-bar app

The native menu-bar frontend for [AI Usage Indicator](../../README.md). It is a
SwiftUI app—no SwiftBar host required—and renders the separately installed local core's
JSON contract.

- Menu-bar line: `🧠 15% · 41° · 54%` (CPU · temperature · RAM), with a warning
  marker when configured thresholds are crossed.
- Panel: host resources, per-provider/account usage and quota windows, and recent
  local AI-tool sessions.
- Refresh: on demand when the panel opens, with a loose three-hour backup timer.

Observed agent/provider source data is read-only. The app can write only its own
preferences and login-item setting; the Python core can write its own cache,
event log, and diff baseline under the configured state directory.

## Build and run

```bash
swift build -c release
.build/release/AIUsageIndicator
```

A menu-bar item appears; there is no dock icon. Quit from the panel.

Install the canonical CLI first and verify its JSON payload:

```bash
ai-usage-indicator menubar --json
```

During the transition, the app also accepts the legacy `shbr` executable. It
prefers `ai-usage-indicator` when both are present.

## Architecture

This Swift package is a renderer. The Python core remains headless and
stdlib-only.

- **Developer preview:** shells out to `ai-usage-indicator menubar --json`, with
  `shbr` as a compatibility fallback.
- **Distribution milestone:** bundle a frozen core so the `.app` no longer
  requires Python or a separate CLI install.

| file | role |
|------|------|
| `Sources/SHawnBrain/Snapshot.swift` | Codable mirror of the menu-bar JSON contract |
| `Sources/SHawnBrain/BrainModel.swift` | resolves/runs the CLI, decodes data, controls refresh |
| `Sources/SHawnBrain/SHawnBrainApp.swift` | `@main` menu-bar and accessory-window scenes |
| `Sources/SHawnBrain/BrainFrames.swift` | menu-bar animation frames |
| `Sources/SHawnBrain/ContentView.swift` | panel and settings UI |

Regenerate menu-bar frames after source artwork changes:

```bash
python3 scripts/render_brain_frames.py --write-swift
```

`Snapshot.swift` and `shbr.engine.menubar_data()` are the two ends of one public
contract; schema changes must keep both in sync.
