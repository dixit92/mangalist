"""Render the app icon (mangalist/gui/app_icon.py, the same renderer the running app uses) into files:

    build/icons/MangaList.png      1024 px (macOS: PyInstaller turns it into .icns)
    build/icons/MangaList-256.png  256 px (Linux AppImage / .desktop)
    build/icons/MangaList.ico      16-256 px (Windows exe and installer)

With --repo it (re)writes the committed copies instead, after a change to the icon:

    packaging/icons/mangalist-icon.svg   the master SVG (browsers apply its clip)
    packaging/icons/mangalist-512.png    the container's browser icon and the Unraid label

Needs PySide6 and Pillow; runs headless (QT_QPA_PLATFORM=offscreen is set if unset).
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "icons"
REPO = ROOT / "packaging" / "icons"


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from mangalist.gui.app_icon import ICON_SVG, render_app_icon

    app = QApplication.instance() or QApplication([])  # noqa: F841 - QPixmap needs an application

    def png_bytes(size: int) -> bytes:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        render_app_icon(size).save(buf, "PNG")
        return bytes(buf.data())

    if "--repo" in sys.argv[1:]:
        REPO.mkdir(parents=True, exist_ok=True)
        (REPO / "mangalist-icon.svg").write_text(ICON_SVG, encoding="utf-8")
        (REPO / "mangalist-512.png").write_bytes(png_bytes(512))
        for f in sorted(REPO.iterdir()):
            print(f"{f.relative_to(ROOT)}  {f.stat().st_size} bytes")
        return 0

    OUT.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    (OUT / "MangaList.png").write_bytes(png_bytes(1024))
    (OUT / "MangaList-256.png").write_bytes(png_bytes(256))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [Image.open(io.BytesIO(png_bytes(s))) for s in sizes]
    frames[-1].save(OUT / "MangaList.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
    for f in sorted(OUT.iterdir()):
        print(f"{f.relative_to(ROOT)}  {f.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
