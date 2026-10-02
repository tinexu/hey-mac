#!/usr/bin/env bash
# Builds "Hey Mac.app" in ~/Applications so it runs without Terminal.
# Run from the project folder:  ./scripts/make_app.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT="$(pwd)"
APP="$HOME/Applications/Hey Mac.app"

[[ -x "$PROJECT/.venv/bin/python" ]] || { echo "Run ./scripts/setup.sh first."; exit 1; }

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$HOME/Library/Logs"

# The app just starts your venv's Python in the project folder, logging to a file.
cat > "$APP/Contents/MacOS/HeyMac" <<EOF
#!/bin/bash
cd "$PROJECT"
exec "$PROJECT/.venv/bin/python" -m heymac >> "\$HOME/Library/Logs/heymac.log" 2>&1
EOF
chmod +x "$APP/Contents/MacOS/HeyMac"

cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Hey Mac</string>
  <key>CFBundleDisplayName</key><string>Hey Mac</string>
  <key>CFBundleIdentifier</key><string>local.heymac</string>
  <key>CFBundleExecutable</key><string>HeyMac</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>0.1</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Hey Mac listens for your voice commands.</string>
  <key>NSAppleEventsUsageDescription</key><string>Hey Mac opens and quits apps for you.</string>
</dict></plist>
EOF

# Ad-hoc sign so macOS remembers the permissions you grant.
codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true

echo "Built: $APP"
read -r -p "Start Hey Mac automatically when you log in? [y/N] " yn
if [[ "$yn" =~ ^[Yy] ]]; then
  osascript -e "tell application \"System Events\" to make login item at end with properties {path:\"$APP\", hidden:true}" >/dev/null
  echo "Added to Login Items."
fi
open "$APP"
echo "Started. Logs: ~/Library/Logs/heymac.log"