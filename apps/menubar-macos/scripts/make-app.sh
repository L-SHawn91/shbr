#!/bin/zsh
# Package the release binary into a proper double-clickable SHawnBrain.app bundle.
# Phase A: the bundle still shells out to `shbr` on PATH (installed separately).
# Usage: ./scripts/make-app.sh [--install]   (--install copies to /Applications)
set -euo pipefail

HERE="${0:A:h}"
ROOT="${HERE:h}"                 # apps/menubar-macos
APP="SHawnBrain"
BUNDLE_ID="com.shawn.shawnbrain"
VERSION="0.1.0"

cd "$ROOT"
echo "› building release…"
swift build -c release >/dev/null
BIN="$(swift build -c release --show-bin-path)/$APP"
[[ -x "$BIN" ]] || { echo "!! binary not found at $BIN"; exit 1; }

DIST="$ROOT/dist"
APPDIR="$DIST/$APP.app"
echo "› assembling $APP.app…"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/Contents/MacOS" "$APPDIR/Contents/Resources"
cp "$BIN" "$APPDIR/Contents/MacOS/$APP"

cat > "$APPDIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>$APP</string>
  <key>CFBundleDisplayName</key>     <string>SHawn Brain</string>
  <key>CFBundleIdentifier</key>      <string>$BUNDLE_ID</string>
  <key>CFBundleExecutable</key>      <string>$APP</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>$VERSION</string>
  <key>CFBundleVersion</key>         <string>$VERSION</string>
  <key>LSMinimumSystemVersion</key>  <string>13.0</string>
  <key>LSUIElement</key>             <true/>
  <key>NSHighResolutionCapable</key> <true/>
  <key>NSHumanReadableCopyright</key><string>SHawn Brain — read-only agent observer</string>
</dict>
</plist>
PLIST

# ad-hoc sign so Gatekeeper/launchd accept the local bundle (no notarization yet)
codesign --force --deep --sign - "$APPDIR" >/dev/null 2>&1 || \
  echo "   (codesign skipped — bundle still runnable locally)"

echo "✓ built: $APPDIR"

if [[ "${1:-}" == "--install" ]]; then
  DEST="/Applications/$APP.app"
  echo "› installing to $DEST…"
  rm -rf "$DEST"
  cp -R "$APPDIR" "$DEST"
  echo "✓ installed: $DEST"
fi
