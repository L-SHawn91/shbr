#!/bin/zsh
# Package the release binary into a proper double-clickable SHawnBrain.app bundle.
# Phase A: the bundle still shells out to `shbr` on PATH (installed separately).
#
# Channel (SHBR_CHANNEL): "dev" (default) = the version you run daily, ad-hoc
# signed, bundle id ….dev + name "SHawn Brain (dev)" so it coexists in the menu
# bar with the shipped build. "release" = the public build (clean id + name);
# release.sh drives that path with Developer ID signing + notarization.
#
# Usage: SHBR_CHANNEL=dev|release ./scripts/make-app.sh [--install]
set -euo pipefail

HERE="${0:A:h}"
ROOT="${HERE:h}"                 # apps/menubar-macos
APP="SHawnBrain"
VERSION="0.1.0"                  # CFBundleShortVersionString — clean x.y.z (Apple spec)

# ── channel: dev (personal daily driver) vs release (public build) ───────────
CHANNEL="${SHBR_CHANNEL:-dev}"
BASE_BUNDLE_ID="com.shawn.shawnbrain"
BUILD_SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [[ "$CHANNEL" == "release" ]]; then
  BUNDLE_ID="$BASE_BUNDLE_ID"
  DISPLAY_NAME="SHawn Brain"
else
  CHANNEL="dev"
  BUNDLE_ID="$BASE_BUNDLE_ID.dev"
  DISPLAY_NAME="SHawn Brain (dev)"
fi
echo "› channel: $CHANNEL  ($BUNDLE_ID)"

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

# 앱 번들 아이콘 (SHawn 두뇌 로고). 있으면 복사하고 Info.plist에 CFBundleIconFile로 건다.
ICON_SRC="$ROOT/Resources/AppIcon.icns"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "$APPDIR/Contents/Resources/AppIcon.icns"
  ICON_PLIST='  <key>CFBundleIconFile</key>        <string>AppIcon</string>'
else
  ICON_PLIST=''
fi

cat > "$APPDIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>            <string>$APP</string>
  <key>CFBundleDisplayName</key>     <string>$DISPLAY_NAME</string>
  <key>CFBundleIdentifier</key>      <string>$BUNDLE_ID</string>
  <key>SHBRChannel</key>             <string>$CHANNEL</string>
  <key>SHBRBuild</key>               <string>$BUILD_SHA</string>
  <key>CFBundleExecutable</key>      <string>$APP</string>
  <key>CFBundlePackageType</key>     <string>APPL</string>
  <key>CFBundleShortVersionString</key> <string>$VERSION</string>
  <key>CFBundleVersion</key>         <string>$VERSION</string>
  <key>LSMinimumSystemVersion</key>  <string>13.0</string>
  <key>LSUIElement</key>             <true/>
$ICON_PLIST
  <key>NSHighResolutionCapable</key> <true/>
  <key>NSHumanReadableCopyright</key><string>SHawn Brain — read-only agent observer</string>
</dict>
</plist>
PLIST

# Sign the bundle. Default = ad-hoc ("-") for local dev. For distribution, set
# CODESIGN_ID to a "Developer ID Application: …" identity → adds Hardened Runtime
# (--options runtime) + secure timestamp, both REQUIRED for notarization.
# release.sh drives that path; here we just honor the env var.
SIGN_ID="${CODESIGN_ID:--}"
if [[ "$SIGN_ID" == "-" ]]; then
  codesign --force --sign - "$APPDIR" >/dev/null 2>&1 || \
    echo "   (codesign skipped — bundle still runnable locally)"
  echo "✓ built (ad-hoc signed): $APPDIR"
else
  # Sign inside-out: nested Mach-O first, then the bundle. No --deep (Apple
  # deprecates it for distribution). Only one nested executable here.
  codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APPDIR/Contents/MacOS/$APP"
  codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APPDIR"
  echo "✓ built (Developer ID signed, hardened runtime): $APPDIR"
fi

if [[ "${1:-}" == "--install" ]]; then
  DEST="/Applications/$APP.app"
  echo "› installing to $DEST…"
  rm -rf "$DEST"
  cp -R "$APPDIR" "$DEST"
  echo "✓ installed: $DEST"
fi
