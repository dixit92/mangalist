"""Settings > Logging: the level, the per-area levels and the log files' size apply at once and are stored; the log
folder can be opened or its path copied. The global logging state is restored after every test."""

from __future__ import annotations

import logging
import logging.handlers

import pytest

pytest.importorskip("PySide6")

from mangalist import log_config, paths, store  # noqa: E402
from mangalist.gui.settings_sections import LoggingPage  # noqa: E402

from .conftest import qapp  # noqa: E402,F401


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def logs():
    root = logging.getLogger()
    saved = (list(root.handlers), root.level)
    log_config.shutdown()
    log_config.setup()
    yield log_config
    log_config.shutdown()
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def make(db, **kw):
    kw.setdefault("env", {})
    return LoggingPage(db, **kw)


def test_it_starts_on_info_with_every_area_following_it(qapp, db, logs):
    page = make(db)
    assert page.level_combo.currentData() == "info" and page.level_combo.currentText() == "Info"
    assert [page.level_combo.itemText(i) for i in range(4)] == ["Error", "Warning", "Info", "Debug"]
    assert all(combo.currentData() == "" for combo in page.area_combos.values())
    assert list(page.area_combos) == list(log_config.AREA_KEYS)
    assert (page.size_spin.value(), page.keep_spin.value()) == (2, 5)
    assert "usual choice" in page.level_help.text()


def test_a_new_level_is_stored_and_applies_at_once(qapp, db, logs):
    page = make(db)
    log = logging.getLogger("mangalist.scanner")
    log.debug("hidden at info")
    page.level_combo.setCurrentIndex(page.level_combo.findData("debug"))
    assert db.get_setting("log_level") == "debug" and log_config.current().level == "debug"
    log.debug("shown at debug")
    for h in logging.getLogger().handlers:
        h.flush()
    text = (paths.log_dir() / "mangalist.log").read_text(encoding="utf-8")
    assert "shown at debug" in text and "hidden at info" not in text
    assert page.level_combo.currentText() == "Debug"


def test_an_area_level_and_the_file_limits_are_stored_and_applied(qapp, db, logs):
    page = make(db)
    nyaa = page.area_combos["nyaa"]
    nyaa.setCurrentIndex(nyaa.findData("debug"))
    page.size_spin.setValue(10)
    page.keep_spin.setValue(1)
    assert db.get_setting("log_areas") == {"nyaa": "debug"}
    assert (db.get_setting("log_max_mb"), db.get_setting("log_backups")) == (10, 1)
    handler = [h for h in logging.getLogger().handlers if isinstance(h, logging.handlers.RotatingFileHandler)][0]
    assert (handler.maxBytes, handler.backupCount) == (10 * 1024 * 1024, 1)
    assert log_config.current().areas == {"nyaa": "debug"}
    assert "Up to 20 MB on disk" in page.space_label.text() and "plus 1 old file)" in page.space_label.text()
    nyaa.setCurrentIndex(0)                                               # back to "Same as above"
    assert db.get_setting("log_areas") == {}


def test_the_page_shows_what_is_stored_and_the_variable_only_seeds_it(qapp, db, logs):
    assert make(db, env={"MANGALIST_LOG_LEVEL": "warning"}).level_combo.currentData() == "warning"
    log_config.save(db, log_config.LogSettings(level="error", areas={"scan": "debug"}, max_mb=4, backups=2))
    page = make(db, env={"MANGALIST_LOG_LEVEL": "warning"})
    assert page.level_combo.currentData() == "error" and page.area_combos["scan"].currentData() == "debug"
    assert (page.size_spin.value(), page.keep_spin.value()) == (4, 2)


def test_open_and_copy_the_log_folder(qapp, db, logs):
    opened, copied = [], []
    page = make(db, open_folder=lambda folder: opened.append(folder) or True, copy_text=copied.append)
    assert page.folder_label.text() == str(paths.log_dir())
    page.btn_open.click()
    page.btn_copy.click()
    assert opened == [str(paths.log_dir())] and paths.log_dir().is_dir(), "created when nothing was logged yet"
    assert copied == [str(paths.log_dir())] and "copied" in page.folder_status.text()


def test_a_folder_that_cannot_be_opened_says_so_and_points_at_the_path(qapp, db, logs):
    page = make(db, open_folder=lambda folder: False)
    assert page.open_log_folder() is False
    assert "Could not open" in page.folder_status.text() and "copy it" in page.folder_status.text()
    assert page.folder_status.property("tone") == "bad"
