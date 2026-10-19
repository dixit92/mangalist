"""Every test gets its own data folder, so no test reads or writes the user's real settings,
cache or logs (and none writes next to the source tree)."""

from __future__ import annotations

import faulthandler
import os
import sys

import pytest

from mangalist import paths


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "data"))
    # The long counts run in this process unless a test asks for the subprocess pool (tests/test_process_pool.py): a
    # window per test would otherwise start child processes for every Rename-pending count.
    monkeypatch.setenv("MANGALIST_SUBPROCESS", "0")


class _NoMangaDex:
    """MangaDex never answers in a test: the chapter check's own bulk request learns nothing (a test that needs answers
    passes its own client)."""

    def latest_uploads(self, ids, should_stop=None):
        from mangalist.services.mangadex import LatestUploads

        return LatestUploads()

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def _no_mangadex(monkeypatch):
    """No test asks MangaDex itself (the scheduled chapter check's gate, lane GT 2026-10-18)."""
    from mangalist.downloads import chapter_gate

    monkeypatch.setattr(chapter_gate, "default_client", lambda: _NoMangaDex())


@pytest.fixture(scope="session", autouse=True)
def _qt_teardown():
    """Close and delete every leftover top-level widget while the QApplication still exists. Dialogs a test
    created and never deleted otherwise outlive it until Python's final cleanup, where PySide6 6.12 crashes the
    process after a green run ("shared QObject was deleted directly", exit 139). No-op without PySide6 or a
    QApplication (the no-Qt job never imports it)."""
    yield
    import sys

    if "PySide6.QtWidgets" not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


EXIT_DUMP_S = 120


def pytest_unconfigure(config):
    """After the last test: a process (pytest or an xdist worker) that has not exited within EXIT_DUMP_S - a thread or
    a subprocess pool that never stops at exit - dumps every thread's stack and exits with an error, so CI shows where
    it hung instead of waiting for the job's timeout. (``faulthandler_timeout`` in pytest.ini covers a hang inside a
    test.) Only in a process that pytest itself runs (``pytest`` / ``python -m pytest``, an xdist worker) - never in a
    program that calls ``pytest.main()`` and lives on."""
    argv0 = os.path.normcase(os.path.normpath(sys.argv[0] if sys.argv else ""))
    own = (hasattr(config, "workerinput") or os.path.basename(argv0) in ("pytest", "pytest.exe", "py.test")
           or argv0.endswith(os.path.join("pytest", "__main__.py")))
    if not own:
        return
    try:
        faulthandler.dump_traceback_later(EXIT_DUMP_S, exit=True, file=sys.__stderr__)
    except (AttributeError, ValueError, OSError):        # no usable stderr: nothing to dump to
        pass
