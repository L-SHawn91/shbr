#!/usr/bin/env bash
# <xbar.title>AI Usage Indicator</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>SHawn</xbar.author>
# <xbar.desc>Read-only glance at local AI-agent usage/quota + host CPU/mem/temp.</xbar.desc>
# <xbar.dependencies>python3,ai-usage-indicator</xbar.dependencies>
#
# SwiftBar / xbar plugin. The filename encodes the refresh interval:
#   shbr.10s.sh  -> re-runs every 10 seconds.
# Rename to change cadence (e.g. shbr.3s.sh for a snappier host-only meter,
# shbr.1m.sh to go easy on the agent-usage query).
#
# Install:
#   brew install swiftbar          # or: brew install --cask xbar
#   mkdir -p ~/.config/swiftbar-plugins
#   cp contrib/swiftbar/shbr.10s.sh ~/.config/swiftbar-plugins/   # (or symlink)
#   chmod +x ~/.config/swiftbar-plugins/shbr.10s.sh
#   # then point SwiftBar at that folder (first launch asks for it).
#
# Requires `ai-usage-indicator` or legacy `shbr` on PATH. SwiftBar runs plugins
# with a minimal PATH, so the usual install locations are prepended below.

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# The full view includes per-agent token usage read from local on-disk ledgers.
# If that query ever feels slow at this interval, add --no-agents for a pure
# host-resource meter, which reads no agent state at all:
#   exec ai-usage-indicator menubar --no-agents
if command -v ai-usage-indicator >/dev/null 2>&1; then
  exec ai-usage-indicator menubar
elif command -v shbr >/dev/null 2>&1; then
  exec shbr menubar
else
  echo "AI Usage Indicator unavailable"
  echo "---"
  echo "Install the ai-usage-indicator CLI first."
  exit 1
fi
