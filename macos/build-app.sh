#!/bin/bash
set -euo pipefail
MACOS_ROOT="$(cd "$(dirname "$0")" && pwd)"
WORK_ROOT="${GPU_RADAR_BUILD_WORK:-$MACOS_ROOT/../../../work/macos-build}"
mkdir -p "$WORK_ROOT/clang-cache" "$WORK_ROOT/swift-cache" "$MACOS_ROOT/build"
export CLANG_MODULE_CACHE_PATH="$WORK_ROOT/clang-cache"
export SWIFTPM_MODULECACHE_OVERRIDE="$WORK_ROOT/swift-cache"
swift build --package-path "$MACOS_ROOT" --scratch-path "$WORK_ROOT/swift-build" --cache-path "$WORK_ROOT/package-cache" --disable-sandbox -c release
BIN_DIR="$(swift build --package-path "$MACOS_ROOT" --scratch-path "$WORK_ROOT/swift-build" --cache-path "$WORK_ROOT/package-cache" --disable-sandbox -c release --show-bin-path)"
APP_PATH="$MACOS_ROOT/build/GPU Radar.app"
# Sign outside synced Documents: FileProvider can repeatedly inject FinderInfo
# into application bundles there, racing codesign's resource validation.
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/gpu-radar-app.XXXXXX")"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE_APP="$STAGE_ROOT/GPU Radar.app"
mkdir -p "$STAGE_APP/Contents/MacOS" "$STAGE_APP/Contents/Resources"
cp "$BIN_DIR/GPURadar" "$STAGE_APP/Contents/MacOS/GPURadar"
cp "$MACOS_ROOT/Info.plist" "$STAGE_APP/Contents/Info.plist"
"$MACOS_ROOT/build-icon.sh" "$STAGE_APP/Contents/Resources/AppIcon.icns"
xattr -cr "$STAGE_APP"
codesign --force --deep --sign - "$STAGE_APP"
codesign --verify --deep --strict "$STAGE_APP"
mkdir -p "$APP_PATH/Contents/MacOS" "$APP_PATH/Contents/Resources" "$APP_PATH/Contents/_CodeSignature"
# Replace the executable atomically so an already running app retains its image.
cp "$STAGE_APP/Contents/MacOS/GPURadar" "$APP_PATH/Contents/MacOS/GPURadar.new"
mv -f "$APP_PATH/Contents/MacOS/GPURadar.new" "$APP_PATH/Contents/MacOS/GPURadar"
cp "$STAGE_APP/Contents/Info.plist" "$APP_PATH/Contents/Info.plist"
cp "$STAGE_APP/Contents/Resources/AppIcon.icns" "$APP_PATH/Contents/Resources/AppIcon.icns"
cp "$STAGE_APP/Contents/_CodeSignature/CodeResources" "$APP_PATH/Contents/_CodeSignature/CodeResources"
xattr -cr "$APP_PATH"
printf 'Built: %s\n' "$APP_PATH"
