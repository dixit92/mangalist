"""The JSON scheduler state: round trip, atomic save, unreadable file handling."""

from __future__ import annotations

from datetime import datetime

from manga_list.headless.schedule import UTC
from manga_list.headless.state import JobState, StateStore


def test_round_trip(tmp_path):
    store = StateStore(tmp_path / "s.json")
    st = store.get("rescan")
    st.last_run = datetime(2026, 6, 1, 3, 30, tzinfo=UTC)
    st.next_run = datetime(2026, 6, 2, 3, 30, tzinfo=UTC)
    st.last_status, st.runs, st.schedule = "ok", 4, "daily@03:30"
    st.extra = {"roots": [{"series": 3}]}
    store.save()
    back = StateStore(tmp_path / "s.json").load().get("rescan")
    assert back == st
    assert not list(tmp_path.glob("*.tmp")), "no temporary file left behind"


def test_missing_file_is_empty_state(tmp_path):
    assert StateStore(tmp_path / "none.json").load().jobs == {}


def test_unreadable_file_is_kept_aside_and_state_starts_fresh(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{not json", encoding="utf-8")
    store = StateStore(p).load()
    assert store.jobs == {}
    assert (tmp_path / "s.json.unreadable").read_text(encoding="utf-8") == "{not json"


def test_bad_values_are_ignored(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"jobs": {"rescan": {"next_run": "yesterday", "runs": "x", "extra": []}}}',
                 encoding="utf-8")
    st = StateStore(p).load().get("rescan")
    assert st == JobState()


def test_naive_timestamps_are_read_as_utc(tmp_path):
    p = tmp_path / "s.json"
    p.write_text('{"jobs": {"rescan": {"next_run": "2026-06-02T03:30:00"}}}', encoding="utf-8")
    assert StateStore(p).load().get("rescan").next_run == datetime(2026, 6, 2, 3, 30, tzinfo=UTC)


def test_saved_file_gets_the_normal_file_mode(tmp_path):
    import os
    import stat

    if os.name == "nt":
        return
    store = StateStore(tmp_path / "s.json")
    store.get("rescan")
    old = os.umask(0o022)
    try:
        store.save()
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "s.json").stat().st_mode) == 0o644
