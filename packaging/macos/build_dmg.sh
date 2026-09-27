#!/usr/bin/env bash
# macOS package from the PyInstaller bundle (dist/MangaList.app):
#   package/MangaList-v<version>-macos-<arch>.dmg  (drag Manga List to Applications)
# Usage: packaging/macos/build_dmg.sh <version> <arm64|x64>
# The bundle is only ad-hoc signed (PyInstaller does that); releases rely on SHA256SUMS.
set -euo pipefail

VERSION="${1:?usage: build_dmg.sh <version> <arm64|x64>}"
ARCH="${2:?usage: build_dmg.sh <version> <arm64|x64>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

APP="dist/MangaList.app"
NAME="MangaList-v${VERSION}-macos-${ARCH}"
[ -d "$APP" ] || { echo "missing $APP - run pyinstaller first" >&2; exit 1; }

# The binary must be built for the architecture the file name promises.
case "$ARCH" in arm64) want=arm64 ;; x64) want=x86_64 ;; *) echo "arch must be arm64 or x64" >&2; exit 1 ;; esac
have="$(lipo -archs "$APP/Contents/MacOS/MangaList")"
[ "$have" = "$want" ] || { echo "binary is '$have', expected '$want'" >&2; exit 1; }

STAGE="build/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE" package
cp -R "$APP" "$STAGE/Manga List.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Manga List" -srcfolder "$STAGE" -ov -format UDZO "package/$NAME.dmg"
ls -l "package/$NAME.dmg"
