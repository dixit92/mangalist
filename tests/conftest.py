"""Every test gets its own data folder, so no test reads or writes the user's real settings,
cache or logs (and none writes next to the source tree)."""

from __future__ import annotations

import pytest

from mangalist import paths


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "data"))


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
