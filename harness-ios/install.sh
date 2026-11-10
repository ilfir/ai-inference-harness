#!/bin/bash
# Build AI Harness for iPhone and install it on a connected device.
#   harness-ios/install.sh              # first paired iPhone found
#   harness-ios/install.sh "Ilfir iPhone"   # by name or UDID
# Needs Xcode, xcodegen (brew install xcodegen) and a signed-in Apple ID in Xcode (Settings → Accounts).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
TEAM="${AIH_TEAM:-CZ922H54BA}"   # your Apple Development team ID (override with AIH_TEAM=...)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# App icon: drawn from the Mac icon at build time if missing.
ICON=Assets.xcassets/AppIcon.appiconset/icon.png
if [ ! -f "$ICON" ]; then
  swift "$HERE/../harness-ui/macos/icon.swift" /tmp/aih-mac-icon.png >/dev/null
  swift tools/ios-icon.swift /tmp/aih-mac-icon.png "$ICON"
fi

say "1/3  Finding your iPhone"
WANT="${1:-}"
DEV=$(xcrun devicectl list devices 2>/dev/null | awk -v want="$WANT" '
  /iPhone|iPad/ && !/simulated/ && (want == "" || index($0, want)) {
    for (i = 1; i <= NF; i++) if ($i ~ /^[0-9A-F]{8}-[0-9A-F]{16}$|^[0-9A-F-]{25,}$/) { print $i; exit }
  }')
if [ -z "$DEV" ]; then
  cat <<MSG
No iPhone found. Do this on the iPhone, then run this script again:
  1. Plug it into this Mac with a cable and unlock it.
  2. Tap "Trust This Computer" and enter your passcode.
  3. Settings → Privacy & Security → Developer Mode → on (the phone restarts; confirm after restart).
MSG
  exit 1
fi
echo "device: $DEV"

say "2/3  Building"
command -v xcodegen >/dev/null || { echo "Install xcodegen first: brew install xcodegen"; exit 1; }
xcodegen generate >/dev/null
xcodebuild -project AIHarness.xcodeproj -scheme AIHarness -destination "id=$DEV" -configuration Release \
  -derivedDataPath build -allowProvisioningUpdates DEVELOPMENT_TEAM="$TEAM" build -quiet \
  || { echo "Build failed. If it mentions signing: open Xcode → Settings → Accounts and sign in with your Apple ID."; exit 1; }
APP="$HERE/build/Build/Products/Release-iphoneos/AI Harness.app"
# When the signature runs out: a free Apple ID gets 7 days, a paid Apple Developer Program team gets a year.
EXPIRES=$(security cms -D -i "$APP/embedded.mobileprovision" 2>/dev/null | plutil -extract ExpirationDate raw - 2>/dev/null || true)

say "3/3  Installing"
if ! OUT=$(xcrun devicectl device install app --device "$DEV" "$APP" 2>&1); then
  echo "$OUT" | tail -8
  case "$OUT" in
    *paired*|*pair*) echo "
The iPhone must trust this Mac: connect with a cable, unlock it and tap Trust." ;;
    *"Developer Mode"*|*developer\ mode*) echo "
Turn on Settings → Privacy & Security → Developer Mode on the iPhone, restart it, then run again." ;;
    *locked*|*Locked*) echo "
Unlock the iPhone and keep it awake, then run again." ;;
  esac
  exit 1
fi
xcrun devicectl device process launch --device "$DEV" ai.harness.ios >/dev/null 2>&1 || true

cat <<MSG

Installed. Signed until: ${EXPIRES:-unknown} (UTC).
If the app won't open ("Untrusted Developer"):
  iPhone Settings → General → VPN & Device Management → your Apple ID → Trust.
Free Apple IDs expire the app after 7 days: run this script again before then to renew it (chat history is kept).
A paid Apple Developer Program membership signs for a year instead.
MSG
