#!/bin/bash
set -euo pipefail

MACOS_ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_PATH="${1:-$MACOS_ROOT/build/GPU Radar.app}"

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

if [ "$#" -gt 2 ]; then
    printf 'Usage: %s [path/to/GPU Radar.app] [path/to/output.dmg]\n' "$0" >&2
    exit 1
fi
[ "$(uname -s)" = Darwin ] || fail 'DMG packaging requires macOS.'
[ -d "$APP_PATH" ] || fail "App not found: $APP_PATH. Run macos/build-app.sh first."
APP_PATH="$(cd "$(dirname "$APP_PATH")" && pwd)/$(basename "$APP_PATH")"
APP_NAME="$(basename "$APP_PATH")"
[[ "$APP_NAME" == *.app ]] || fail 'The input must be an .app bundle.'

# Work outside synced Documents so FileProvider metadata cannot race signing
# validation. Only this copy is cleaned; the original app and signature remain.
STAGE_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/gpu-radar-dmg.XXXXXX")"
PUBLISH_ROOT=''
cleanup() {
    rm -rf "$STAGE_ROOT"
    if [ -n "$PUBLISH_ROOT" ]; then
        rm -rf "$PUBLISH_ROOT"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

CONTENTS="$STAGE_ROOT/contents"
mkdir -p "$CONTENTS"
STAGE_APP="$CONTENTS/$APP_NAME"
/usr/bin/ditto --norsrc --noextattr "$APP_PATH" "$STAGE_APP"
/usr/bin/xattr -cr "$STAGE_APP"
/usr/bin/codesign --verify --deep --strict "$STAGE_APP"

PLIST="$STAGE_APP/Contents/Info.plist"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$PLIST")"
EXECUTABLE="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$PLIST")"
MINIMUM_MACOS="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$PLIST")"
[[ "$VERSION" =~ ^[A-Za-z0-9._+-]+$ ]] || fail 'The app version contains unsupported filename characters.'
[[ "$EXECUTABLE" != */* && -n "$EXECUTABLE" ]] || fail 'Invalid CFBundleExecutable.'
ARCHITECTURES="$(/usr/bin/lipo -archs "$STAGE_APP/Contents/MacOS/$EXECUTABLE")"
read -r -a ARCH_LIST <<< "$ARCHITECTURES"
[ "${#ARCH_LIST[@]}" -gt 0 ] || fail 'No executable architecture found.'
HAS_ARM64=false
HAS_X86_64=false
for architecture in "${ARCH_LIST[@]}"; do
    case "$architecture" in
        arm64) HAS_ARM64=true ;;
        x86_64) HAS_X86_64=true ;;
        *) fail "Unsupported app architecture: $architecture" ;;
    esac
done
if [ "$HAS_ARM64" = true ] && [ "$HAS_X86_64" = true ]; then
    ARCH_LABEL=universal
    ARCH_ZH='Apple Silicon 和 Intel Mac'
    ARCH_EN='Apple Silicon and Intel Macs'
elif [ "$HAS_ARM64" = true ]; then
    ARCH_LABEL=arm64
    ARCH_ZH='Apple Silicon Mac（M 系列芯片）'
    ARCH_EN='Apple Silicon Macs (M-series chips)'
else
    ARCH_LABEL=x86_64
    ARCH_ZH='Intel Mac'
    ARCH_EN='Intel Macs'
fi

OUTPUT_PATH="${2:-$MACOS_ROOT/build/GPU-Radar-$VERSION-macOS-$ARCH_LABEL.dmg}"
[[ "$OUTPUT_PATH" == *.dmg ]] || fail 'The output filename must end in .dmg.'
[ ! -d "$OUTPUT_PATH" ] || fail 'The output path is a directory.'
mkdir -p "$(dirname "$OUTPUT_PATH")"
OUTPUT_PATH="$(cd "$(dirname "$OUTPUT_PATH")" && pwd)/$(basename "$OUTPUT_PATH")"

ln -s /Applications "$CONTENTS/Applications"
cat > "$CONTENTS/INSTALL.txt" <<EOF
GPU Radar $VERSION

安装
1. 将 $APP_NAME 拖到 Applications 文件夹。
2. 从“应用程序”打开 GPU Radar，然后推出此磁盘映像。
更新前，请先退出正在运行的 GPU Radar，再替换应用程序。
系统要求：macOS ${MINIMUM_MACOS} 或更新版本；${ARCH_ZH}。

当前公开构建使用 ad-hoc 签名，尚未使用 Apple Developer ID 签名或公证。
macOS Gatekeeper 可能阻止首次打开；此安装包不保证直接通过安全检查。
请仅安装来自你信任的 GPU Radar 发布者的文件。

Installation
1. Drag $APP_NAME into the Applications folder.
2. Open GPU Radar from Applications, then eject this disk image.
Before updating, quit the running GPU Radar app, then replace it.
Requirements: macOS $MINIMUM_MACOS or later; $ARCH_EN.

The current public build is ad-hoc signed, without Apple Developer ID
signing or notarization. macOS Gatekeeper may prevent the first launch;
this package does not guarantee automatic acceptance by Gatekeeper.
Only install files from a GPU Radar publisher you trust.
EOF

STAGED_DMG="$STAGE_ROOT/GPU-Radar.dmg"
/usr/bin/hdiutil create -volname "GPU Radar $VERSION" -srcfolder "$CONTENTS" \
    -fs HFS+ -format UDZO -ov "$STAGED_DMG"
/usr/bin/hdiutil verify "$STAGED_DMG"

# Copy the verified image into a private temporary directory on the destination
# filesystem, then rename it atomically. An older package survives any failure.
PUBLISH_ROOT="$(mktemp -d "$(dirname "$OUTPUT_PATH")/.gpu-radar-dmg.XXXXXX")"
cp "$STAGED_DMG" "$PUBLISH_ROOT/package.dmg"
cmp -s "$STAGED_DMG" "$PUBLISH_ROOT/package.dmg" || fail 'The package copy did not match.'
mv -f "$PUBLISH_ROOT/package.dmg" "$OUTPUT_PATH"
printf 'Packaged: %s\nArchitectures: %s\n' "$OUTPUT_PATH" "$ARCHITECTURES"
