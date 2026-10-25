"""A series whose source keeps failing in the scheduled chapter check (owner-approved 2026-10-24): the failures in a
row are counted per series, reset by a read (or by another source), and from three checks on the check logs a warning
and the Download tab can say so. A fake Suwayomi, made-up names."""

from __future__ import annotations

import logging

import pytest

from mangalist.downloads import chapter_check as cc
from mangalist.downloads import chapter_failures as cf
from mangalist.store.findings import FindingStore

from .test_chapter_check import check, lib, saved  # noqa: F401 - the fixtures


def break_source(w, message="Cloudflare wall"):
    def fail(manga_id):
        raise RuntimeError(message)

    w.suwayomi.chapters = fail


def test_three_checks_in_a_row_that_cannot_read_the_source_warn(db, lib, caplog):
    caplog.set_level(logging.INFO)
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    break_source(lib)
    for expected in (1, 2):
        report = check(db, lib)
        assert report.unreadable == 1 and report.failing == 0 and "could not be read" in report.summary
        assert "for 3+" not in report.summary
        assert cf.get(db, lib.series_id).count == expected and cf.warnings(db) == {}
    report = check(db, lib)
    assert report.unreadable == 1 and report.failing == 1
    assert "1 could not be read (1 for 3+ checks in a row)" in report.summary
    failure = cf.warnings(db)[lib.series_id]
    assert failure.count == 3 and "Cloudflare wall" in failure.reason and failure.source
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "3 checks in a row" in r.getMessage()]
    assert warned and "Change source" in warned[0].getMessage()
    text = lib.backend.chapter_check_warnings()[lib.series_id]
    assert "3 checks in a row" in text and "Cloudflare wall" in text and "Change source…" in text


def test_a_successful_read_loses_the_warning(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    original = lib.suwayomi.chapters
    break_source(lib)
    for _ in range(3):
        check(db, lib)
    assert lib.series_id in cf.warnings(db)
    lib.suwayomi.chapters = original                      # the source recovers
    report = check(db, lib)
    assert report.unreadable == 0 and cf.get(db, lib.series_id) is None and cf.warnings(db) == {}


def test_a_success_between_failures_starts_the_count_again(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    original = lib.suwayomi.chapters
    break_source(lib)
    check(db, lib)
    check(db, lib)
    lib.suwayomi.chapters = original
    check(db, lib)
    break_source(lib)
    check(db, lib)
    assert cf.get(db, lib.series_id).count == 1 and cf.warnings(db) == {}


def test_choosing_another_source_clears_the_failures(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    break_source(lib)
    for _ in range(3):
        check(db, lib)
    assert lib.series_id in lib.backend.chapter_check_warnings()
    lib.backend.forget_match(lib.series_id)
    assert lib.backend.chapter_check_warnings() == {} and cf.get(db, lib.series_id) is None


def test_suwayomi_being_down_is_not_a_series_failure(db, lib):
    saved(lib)
    FindingStore(db).set_mode([lib.series_id], "notify")
    lib.suwayomi.unreachable = True
    for _ in range(4):
        check(db, lib)
    assert cf.get(db, lib.series_id) is None


def test_the_failure_note_is_one_short_row_per_series(db):
    cf.record_failure(db, 7, "x" * 1000, "Some Source")
    cf.record_failure(db, 7, "y" * 1000, "Some Source")
    f = cf.get(db, 7)
    assert f.count == 2 and len(f.reason) <= cf.MAX_REASON and f.reason.startswith("y")
    cf.clear(db, 7)
    assert cf.get(db, 7) is None
    db.set_meta(cf.PREFIX + "9", "not json")
    db.set_meta(cf.PREFIX + "abc", "{}")
    assert cf.warnings(db) == {}
