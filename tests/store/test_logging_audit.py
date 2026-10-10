"""The audit's lines: user-visible store actions are INFO, a setting's value is never logged, a scan ends with one
INFO summary line."""

from __future__ import annotations

import logging

from mangalist.scanner import scan_and_record_library

from .conftest import make_archive


def _messages(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level]


def test_roots_added_changed_and_removed_are_logged(db, library, caplog):
    caplog.set_level(logging.DEBUG)
    root = db.add_root(str(library), "Manga", exclusions=["@Oneshots/**"])
    root.name = "Manga 2"
    db.update_root(root)
    db.remove_root(root.id)
    info = _messages(caplog, logging.INFO)
    assert any(m.startswith("Root added: Manga (") and "1 exclusion" in m for m in info)
    assert any(m.startswith("Root changed: Manga 2") for m in info)
    assert any("Root removed" in m and "files are untouched" in m for m in info)


def test_a_setting_is_logged_by_key_never_by_value(db, caplog):
    caplog.set_level(logging.DEBUG)
    db.set_settings({"some_secret_setting": "value-that-must-not-show", "other": 1})
    assert "other, some_secret_setting" in caplog.text
    assert "value-that-must-not-show" not in caplog.text


def test_a_recorded_scan_ends_with_one_info_summary(db, library, caplog):
    make_archive(library / "Series A" / "Series A v01.cbz")
    make_archive(library / "Series A" / "Series A v02.cbz")
    root = db.add_root(str(library), "Manga")
    caplog.set_level(logging.INFO)
    result = scan_and_record_library([root], db)
    summary = [m for m in _messages(caplog, logging.INFO) if m.startswith("Scan:")]
    assert len(summary) == 1 and "1 root, 1 series, 2 archives" in summary[0]
    assert not result.errors


def test_an_unreadable_root_is_a_warning(db, tmp_path, caplog):
    root = db.add_root(str(tmp_path / "gone"), "Offline")
    caplog.set_level(logging.INFO)
    result = scan_and_record_library([root], db)
    assert result.errors
    assert any("Scan: cannot read the root Offline" in m for m in _messages(caplog, logging.WARNING))
