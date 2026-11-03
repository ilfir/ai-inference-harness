#!/bin/bash
# Build "AI Harness.app" from source (nothing prebuilt is kept in the repo).
#
#   harness-ui/macos/build-app.sh            -> build/AI Harness.app
#   harness-ui/macos/build-app.sh --install  -> also copies it to ~/Applications
#
# Needs the Xcode Command Line Tools (swiftc, /usr/bin/python3). The app bundles harness-ui/ plus the
# harness/, inference/ and client/ folders it syncs to the inference host.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
OUT="$REPO/build"
APP="$OUT/AI Harness.app"
RES="$APP/Contents/Resources/harness"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$RES"

swiftc -O -o "$APP/Contents/MacOS/AI Harness" "$HERE/main.swift" -framework Cocoa -framework WebKit

# App icon: drawn by icon.swift at build time (no image files in the repo).
ICONTMP="$(mktemp -d)"
swift "$HERE/icon.swift" "$ICONTMP/icon.png" >/dev/null
mkdir -p "$ICONTMP/AppIcon.iconset"
for s in 16 32 128 256 512; do
  sips -z $s $s "$ICONTMP/icon.png" --out "$ICONTMP/AppIcon.iconset/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$ICONTMP/icon.png" --out "$ICONTMP/AppIcon.iconset/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONTMP/AppIcon.iconset" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$ICONTMP"

rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' --exclude 'settings.json' --exclude 'chats.json' \
  --exclude 'macos' "$REPO/harness-ui" "$RES/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '.DS_Store' --exclude '*.local.env' \
  "$REPO/harness" "$REPO/inference" "$REPO/client" "$RES/"

VERSION="$(git -C "$REPO" describe --always --dirty 2>/dev/null || echo dev)"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>AI Harness</string>
  <key>CFBundleDisplayName</key><string>AI Harness</string>
  <key>CFBundleExecutable</key><string>AI Harness</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundleIdentifier</key><string>ai.harness.ui</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSAppTransportSecurity</key><dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
EOF

# Ad-hoc signature so Gatekeeper treats the locally built app as one unit.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
echo "Built: $APP"

if [ "${1:-}" = "--install" ]; then
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
  rm -rf "$DEST/AI Harness.app"
  cp -R "$APP" "$DEST/"
  echo "Installed: $DEST/AI Harness.app"
fi
