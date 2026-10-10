"""Entry point: ``python -m mangalist``.

Options (for packaging checks and CI):
  --version      print the version and exit (no Qt import, no window)
  --smoke-test   build the main window (themed, fonts loaded), run the event loop once and exit 0
                 (use with QT_QPA_PLATFORM=offscreen on a headless machine; no library scan)
  --headless     run the scheduled batch jobs without a window (no Qt import);
                 see mangalist/headless/runner.py for its own options
"""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        from mangalist import __version__

        print(f"MangaList {__version__}")
        return 0
    if "--headless" in args:
        from mangalist.headless.runner import main as headless_main

        return headless_main(args)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from mangalist import log_config, paths
    from mangalist.gui.app_icon import build_app_icon
    from mangalist.gui.main_window import MainWindow
    from mangalist.gui.theme import apply_theme

    log_config.setup()
    # Once: copy settings and cache from the pre-per-user location (data/ next to the program).
    try:
        paths.migrate_legacy_data()
    except OSError:
        logging.getLogger(__name__).warning("Could not migrate the old data folder", exc_info=True)
    log_config.apply_stored()          # the owner's log level (Settings > Logging); INFO until the database is read
    logging.getLogger(__name__).info("Data folder: %s", paths.data_dir())
    # On Windows, set an explicit AppUserModelID so the taskbar uses our icon
    # instead of grouping under the generic python.exe icon.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "com.lifepixer.MangaList"
            )
        except (AttributeError, OSError):
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("MangaList")
    app.setWindowIcon(build_app_icon())
    if not apply_theme(app):
        logging.getLogger(__name__).warning("The bundled fonts did not load; using the system font")
    win = MainWindow()
    win.show()
    if "--smoke-test" in args:
        QTimer.singleShot(0, app.quit)
    else:
        # The library at start without a click: scan the library folders in the background.
        QTimer.singleShot(0, win.start_initial_scan)
    code = app.exec()
    win.close()                     # stop the background work (a scan, signatures) before the application goes
    return code


if __name__ == "__main__":
    sys.exit(main())
