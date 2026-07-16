#!/usr/bin/env bash
# <xbar.title>shbr — SHawn Brain</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>SHawn</xbar.author>
# <xbar.desc>Read-only glance at local AI-agent usage/quota + host CPU/mem/temp.</xbar.desc>
# <xbar.dependencies>python3,shbr</xbar.dependencies>
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
# Requires `shbr` on PATH. SwiftBar runs plugins with a minimal PATH, so the
# usual install locations are prepended below.

export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# The full view includes per-agent token usage read from local on-disk ledgers.
# If that query ever feels slow at this interval, add --no-agents for a pure
# host-resource meter, which reads no agent state at all:
#   exec shbr menubar --no-agents
exec shbr menubar
