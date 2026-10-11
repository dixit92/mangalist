"""Schema 7: a series' Off / Notify / Auto mode, findings (offer, skip-not-again, retire, compare-and-set) and runs."""

from __future__ import annotations

import pytest

from mangalist.store.findings import (GONE, MODE_AUTO, MODE_NOTIFY, MODE_OFF, SENT, SKIPPED, WAITING, FindingConflict,
                                      FindingStore, NewFinding)


@pytest.fixture
def two_series(db, library):
    root = db.add_root(str(library), "Manga")
    for name in ("Series A", "Series B"):
        (library / name).mkdir()
        (library / name / "Ch. 0001.00.cbz").write_bytes(b"x")
    from mangalist.scanner import record_library_scan, scan_library

    record_library_scan(db, scan_library(db.list_roots(), db=db))
    return [db.get_series(root.id, "Series A").id, db.get_series(root.id, "Series B").id]


def test_schema_7(db):
    assert db.schema_version() >= 7


def test_modes_follow_the_default_until_set(db, two_series):
    fs = FindingStore(db)
    a, b = two_series
    assert fs.default_mode() == MODE_NOTIFY and fs.mode(a) == MODE_NOTIFY and fs.own_mode(a) is None
    assert fs.set_mode([a], MODE_AUTO) == 1
    assert fs.modes() == {a: MODE_AUTO, b: MODE_NOTIFY} and fs.modes([b]) == {b: MODE_NOTIFY}
    fs.set_default_mode(MODE_OFF)
    assert fs.mode(b) == MODE_OFF and fs.mode(a) == MODE_AUTO
    fs.set_mode([a, b], None)
    assert fs.own_mode(a) is None and fs.mode(a) == MODE_OFF
    with pytest.raises(ValueError):
        fs.set_mode([a], "sometimes")


def test_a_skipped_pick_is_never_offered_again(db, two_series):
    fs = FindingStore(db)
    a, _b = two_series
    run = fs.start_run("schedule")
    f, fresh = fs.offer(NewFinding(a, "volumes", "nyaa:abc", "Series A v01-03", fills=("1", "2", "3"), size_bytes=10,
                                   reason="the series is on Notify", payload={"x": 1}), run)
    assert fresh and f.status == WAITING and f.fills == ("1", "2", "3") and f.payload == {"x": 1} and f.run_id == run
    again, fresh = fs.offer(NewFinding(a, "volumes", "nyaa:abc", "Series A v01-03 (v2)", fills=("2", "3")))
    assert not fresh and again.id == f.id and again.title.endswith("(v2)") and again.fills == ("2", "3")
    assert fs.count_waiting() == 1
    fs.set_status(f.id, SKIPPED)
    same, fresh = fs.offer(NewFinding(a, "volumes", "nyaa:abc", "Series A v01-03"))
    assert not fresh and same.status == SKIPPED and fs.count_waiting() == 0
    with pytest.raises(FindingConflict):
        fs.set_status(f.id, SENT)                       # not waiting any more


def test_retire_and_come_back(db, two_series):
    fs = FindingStore(db)
    a, b = two_series
    one, _ = fs.offer(NewFinding(a, "chapters", "chapters:1:5,6", "Ch. 5-6"))
    two, _ = fs.offer(NewFinding(a, "chapters", "chapters:1:7", "Ch. 7"))
    other, _ = fs.offer(NewFinding(b, "chapters", "chapters:1:9", "Ch. 9"))
    assert fs.retire(a, "chapters", keep=["chapters:1:7"]) == 1
    assert fs.get(one.id).status == GONE and fs.get(two.id).status == WAITING and fs.get(other.id).status == WAITING
    back, fresh = fs.offer(NewFinding(a, "chapters", "chapters:1:5,6", "Ch. 5-6"))
    assert fresh and back.id == one.id and back.status == WAITING
    sent = fs.set_status(two.id, SENT, record_id=42)
    assert sent.status == SENT and sent.record_id == 42
    assert [f.id for f in fs.list(series_id=a)] == [one.id] and len(fs.list(status=None)) == 3


def test_runs(db):
    fs = FindingStore(db)
    assert fs.last_run() is None
    rid = fs.start_run("now")
    run = fs.finish_run(rid, series_searched=12, found=3, sent=1, errors=0, summary="12 series searched")
    assert run.trigger == "now" and run.finished_at and fs.last_run() == run
    with pytest.raises(ValueError):
        fs.start_run("whenever")
