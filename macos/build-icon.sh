#!/bin/bash
set -euo pipefail
MACOS_ROOT="$(cd "$(dirname "$0")" && pwd)"
SOURCE="$MACOS_ROOT/Assets/AppIcon.png"
OUTPUT="${1:-$MACOS_ROOT/build/AppIcon.icns}"
[ -f "$SOURCE" ] || { printf 'Missing app icon: %s\n' "$SOURCE" >&2; exit 1; }
ICON_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/gpu-radar-icon.XXXXXX")"
trap 'rm -rf "$ICON_ROOT"' EXIT
ICONSET="$ICON_ROOT/AppIcon.iconset"
mkdir -p "$ICONSET" "$(dirname "$OUTPUT")"
for size in 16 32 128 256 512; do
    /usr/bin/sips -z "$size" "$size" "$SOURCE" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    retina=$((size * 2))
    /usr/bin/sips -z "$retina" "$retina" "$SOURCE" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "$ICONSET" -o "$OUTPUT"
printf 'Built icon: %s\n' "$OUTPUT"
