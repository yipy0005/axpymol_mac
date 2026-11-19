#!/bin/bash
# Build "AxPyMOL Helper.app" and a drag-to-Applications .dmg into build/. Needs Xcode command line tools.
# Signing: ad-hoc by default. For distribution set SIGN_ID="Developer ID Application: …" (and notarize).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=build; APP="$OUT/AxPyMOL Helper.app"; VER=$(/usr/libexec/PlistBuddy -c "Print CFBundleShortVersionString" app/Info.plist)
rm -rf "$OUT"; mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/bridge" "$APP/Contents/Resources/addin"

for arch in arm64 x86_64; do
  swiftc -O -swift-version 5 -target "$arch-apple-macos13.0" app/main.swift -o "$OUT/helper-$arch" \
    -framework AppKit -framework ServiceManagement && ARCHS+=("$OUT/helper-$arch") || echo "skipping $arch"
done
lipo -create "${ARCHS[@]}" -output "$APP/Contents/MacOS/AxPyMOLHelper"
rm -f "$OUT"/helper-*

cp app/Info.plist "$APP/Contents/Info.plist"
cp bridge/*.py "$APP/Contents/Resources/bridge/"
cp addin/*.html addin/*.js addin/*.png addin/manifest.content.xml "$APP/Contents/Resources/addin/"
codesign --force --deep --options runtime -s "${SIGN_ID:--}" "$APP"

DMG="$OUT/AxPyMOL-Helper-$VER.dmg"; STAGE="$OUT/dmg"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"; ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "AxPyMOL Helper" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
echo "Built $APP and $DMG"
