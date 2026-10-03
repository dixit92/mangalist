# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MangaList (all platforms).

    python packaging/make_icon.py      # optional: build/icons/* (the build works without them)
    pyinstaller MangaList.spec --clean --noconfirm

Output (onedir - an installer around a one-file exe would unpack it to a temp folder on every start):
    Windows / Linux:  dist/MangaList/MangaList[.exe] plus dist/MangaList/_internal/
    macOS:            dist/MangaList.app (windowed bundle) and dist/MangaList/

The version comes from mangalist/_version.py (stamped by packaging/stamp_version.py in CI).
Not code-signed; releases publish SHA256SUMS instead.
"""

import re
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()

VERSION = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]",
                    (ROOT / "mangalist" / "_version.py").read_text(encoding="utf-8")).group(1)
_numeric = re.match(r"\d+(\.\d+){0,2}", VERSION)
NUMERIC = ".".join(((_numeric.group(0) if _numeric else "0.0.0").split(".") + ["0", "0"])[:3])

ICONS = ROOT / "build" / "icons"
if sys.platform == "win32":
    ICON = ICONS / "MangaList.ico"
elif sys.platform == "darwin":
    ICON = ICONS / "MangaList.png"  # converted to .icns by PyInstaller (needs Pillow)
else:
    ICON = None
ICON = str(ICON) if ICON is not None and ICON.exists() else None

a = Analysis(
    ["mangalist/__main__.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "mangalist.gui.main_window",
        "mangalist.gui.table_model",
        "mangalist.gui.mu_worker",
        "mangalist.gui.detail_panel",
        "mangalist.gui.mu_picker",
        "mangalist.matcher",
        "mangalist.mu_cache",
        "mangalist.mu_client",
        "mangalist.mu_match",
        "mangalist.mu_progress",
        "mangalist.models",
        "mangalist.config",
        "mangalist.paths",
        "mangalist._version",
        "mangalist.scanner",
        "mangalist.anilist_client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Qt modules the app does not use (smaller packages).
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtSql",
        "PySide6.QtTest",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebSockets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DExtras",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtSerialPort",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtLocation",
        "PySide6.QtSensors",
        "PySide6.QtTextToSpeech",
        # Build-time only.
        "PIL",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MangaList",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # windowed application (no console window)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="MangaList",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="MangaList.app",
        icon=ICON,
        bundle_identifier="com.lifepixer.MangaList",
        version=NUMERIC,
        info_plist={
            "CFBundleName": "MangaList",
            "CFBundleDisplayName": "MangaList",
            "CFBundleShortVersionString": NUMERIC,
            "CFBundleVersion": NUMERIC,
            "NSHighResolutionCapable": True,
            "LSApplicationCategoryType": "public.app-category.utilities",
        },
    )
