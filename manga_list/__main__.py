"""Entry point: ``python -m manga_list``.

Options (for packaging checks and CI):
  --version      print the version and exit (no Qt import, no window)
  --smoke-test   build the main window, run the event loop once and exit 0
                 (use with QT_QPA_PLATFORM=offscreen on a headless machine)
  --headless     run the scheduled batch jobs without a window (no Qt import);
                 see manga_list/headless/runner.py for its own options
"""

from __future__ import annotations

import logging
import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        from manga_list import __version__

        print(f"Manga List {__version__}")
        return 0
    if "--headless" in args:
        from manga_list.headless.runner import main as headless_main

        return headless_main(args)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from manga_list import log_config, paths
    from manga_list.gui.main_window import MainWindow, _build_app_icon

    log_config.setup()
    # Once: copy settings and cache from the pre-per-user location (data/ next to the program).
    try:
        paths.migrate_legacy_data()
    except OSError:
        logging.getLogger(__name__).warning("Could not migrate the old data folder", exc_info=True)
    logging.getLogger(__name__).info("Data folder: %s", paths.data_dir())
    # On Windows, set an explicit AppUserModelID so the taskbar uses our icon
    # instead of grouping under the generic python.exe icon.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "manga_list.classifier.1"
            )
        except (AttributeError, OSError):
            pass

    app = QApplication(sys.argv)
    app.setApplicationName("Manga List Classifier")
    app.setWindowIcon(_build_app_icon())
    win = MainWindow()
    win.show()
    if "--smoke-test" in args:
        QTimer.singleShot(0, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
