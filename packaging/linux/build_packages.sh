#!/usr/bin/env bash
# Linux packages from the PyInstaller onedir build (dist/MangaList):
#   package/MangaList-v<version>-linux-x64.tar.gz   (fallback: unpack and run ./MangaList)
#   package/MangaList-v<version>-linux-x64.AppImage
# Usage: packaging/linux/build_packages.sh <version>
# appimagetool is downloaded once (pinned release, SHA-256 checked) unless APPIMAGETOOL points to one.
set -euo pipefail

VERSION="${1:?usage: build_packages.sh <version>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

NAME="MangaList-v${VERSION}-linux-x64"
DIST="dist/MangaList"
OUT="package"
ICON="build/icons/MangaList-256.png"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"
APPIMAGETOOL_SHA256="ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0"

[ -x "$DIST/MangaList" ] || { echo "missing $DIST/MangaList - run pyinstaller first" >&2; exit 1; }
[ -f "$ICON" ] || { echo "missing $ICON - run packaging/make_icon.py first" >&2; exit 1; }
mkdir -p "$OUT" build

# --- tar.gz -------------------------------------------------------------------------------
STAGE="build/tar/$NAME"
rm -rf build/tar && mkdir -p "$STAGE"
cp -a "$DIST/." "$STAGE/"
cp README.md LICENSE "$STAGE/"
cp packaging/linux/com.lifepixer.MangaList.desktop "$STAGE/"
cp "$ICON" "$STAGE/com.lifepixer.MangaList.png"
tar -C build/tar -czf "$OUT/$NAME.tar.gz" "$NAME"

# --- AppImage ------------------------------------------------------------------------------
APPDIR="build/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a "$DIST/." "$APPDIR/usr/bin/"
cp packaging/linux/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/linux/com.lifepixer.MangaList.desktop "$APPDIR/com.lifepixer.MangaList.desktop"
cp packaging/linux/com.lifepixer.MangaList.desktop "$APPDIR/usr/share/applications/"
cp "$ICON" "$APPDIR/com.lifepixer.MangaList.png"
cp "$ICON" "$APPDIR/usr/share/icons/hicolor/256x256/apps/com.lifepixer.MangaList.png"

TOOL="${APPIMAGETOOL:-build/appimagetool-x86_64.AppImage}"
if [ ! -x "$TOOL" ]; then
  curl -fsSL -o "$TOOL" "$APPIMAGETOOL_URL"
  echo "$APPIMAGETOOL_SHA256  $TOOL" | sha256sum -c -
  chmod +x "$TOOL"
fi
# Extract-and-run: no FUSE needed on the build machine (containers, CI).
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --no-appstream "$APPDIR" "$OUT/$NAME.AppImage"
chmod +x "$OUT/$NAME.AppImage"

ls -l "$OUT/$NAME.tar.gz" "$OUT/$NAME.AppImage"
