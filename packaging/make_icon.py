"""Render the app icon (the same painter the running app uses) into files for the packages:

    build/icons/MangaList.png      1024 px (macOS: PyInstaller turns it into .icns)
    build/icons/MangaList-256.png  256 px (Linux AppImage / .desktop)
    build/icons/MangaList.ico      16-256 px (Windows exe and installer)

Needs PySide6 and Pillow; runs headless (QT_QPA_PLATFORM=offscreen is set if unset).
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build" / "icons"


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT))
    from PIL import Image
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtWidgets import QApplication

    from mangalist.gui.main_window import render_app_icon

    app = QApplication.instance() or QApplication([])  # noqa: F841 - fonts need an application
    OUT.mkdir(parents=True, exist_ok=True)

    def png_bytes(size: int) -> bytes:
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        render_app_icon(size).save(buf, "PNG")
        return bytes(buf.data())

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
