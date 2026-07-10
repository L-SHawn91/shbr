#!/bin/zsh
# Developer ID sign → notarize → staple → DMG for SHawnBrain (App Store 밖 직접 배포).
#
# ── 최초 1회만, 직접(대화형) 해둘 것 ────────────────────────────────────────
#   1) Apple Developer Program 가입 ($99/년) — developer.apple.com
#   2) "Developer ID Application" 인증서 발급 + 이 맥 키체인에 설치
#        Xcode ▸ Settings ▸ Accounts ▸ Manage Certificates ▸ + ▸ Developer ID Application
#        (설치 확인:  security find-identity -v -p codesigning )
#   3) 공증 자격 1회 저장 (앱 암호는 appleid.apple.com ▸ 로그인/보안에서 생성):
#        xcrun notarytool store-credentials shbr-notary \
#          --apple-id you@example.com --team-id TEAMID --password APP_SPECIFIC_PW
# ────────────────────────────────────────────────────────────────────────────
#
# 사용:
#   CODESIGN_ID="Developer ID Application: Your Name (TEAMID)" \
#   NOTARY_PROFILE=shbr-notary \
#   ./scripts/release.sh
set -euo pipefail

HERE="${0:A:h}"
ROOT="${HERE:h}"
APP="SHawnBrain"
VERSION="0.1.0"
DIST="$ROOT/dist"
APPDIR="$DIST/$APP.app"
DMG="$DIST/$APP-$VERSION.dmg"

: "${CODESIGN_ID:?set CODESIGN_ID to your 'Developer ID Application: …' identity}"
NOTARY_PROFILE="${NOTARY_PROFILE:-shbr-notary}"

# 사전 점검: 인증서가 실제로 키체인에 있는지
if ! security find-identity -v -p codesigning | grep -qF "$CODESIGN_ID"; then
  echo "!! signing identity not found in keychain: $CODESIGN_ID"
  echo "   available:"; security find-identity -v -p codesigning
  exit 1
fi

# 1) 빌드 + 번들 조립 + Developer ID 서명(하드닝 런타임) — make-app.sh에 위임
echo "› build + Developer ID sign: $CODESIGN_ID"
SHBR_CHANNEL=release CODESIGN_ID="$CODESIGN_ID" "$HERE/make-app.sh"

# 서명 + 하드닝 런타임 검증(공증 전 실패를 여기서 잡는다)
codesign --verify --strict --verbose=2 "$APPDIR"
codesign -dvv "$APPDIR" 2>&1 | grep -qi 'flags=.*runtime' || {
  echo "!! hardened runtime flag missing — notarization would fail"; exit 1; }

# 2) 드래그-설치 DMG 조립 후 서명
echo "› assembling DMG…"
STAGE="$DIST/dmg-stage"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APPDIR" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "$APP" -srcfolder "$STAGE" -ov -format ULFO "$DMG" >/dev/null
rm -rf "$STAGE"
codesign --force --sign "$CODESIGN_ID" --timestamp "$DMG"

# 3) 공증: DMG 한 번 제출로 컨테이너 + 내부 .app cdhash 모두 등록된다
echo "› notarizing (몇 분 걸릴 수 있음)…"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

# 4) 스테이플: 오프라인에서도 Gatekeeper 통과하도록 티켓을 앱/​DMG에 박는다
xcrun stapler staple "$APPDIR"
xcrun stapler staple "$DMG"
xcrun stapler validate "$APPDIR"
xcrun stapler validate "$DMG"

echo "✓ notarized + stapled app: $APPDIR"
echo "✓ distributable DMG:       $DMG"
echo "  → 배포용은 이 DMG 파일을 올리면 됨(Gumroad/Paddle/직접 다운로드)."
