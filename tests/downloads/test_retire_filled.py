"""Waiting findings whose numbers are no longer missing leave Found after a filing, without a search (owner,
2026-10-13: chapters sent by hand were filed, yet their old "Change source?" finding still waited). Made-up series."""

from __future__ import annotations

from mangalist.downloads.finder import retire_filled
from mangalist.store.findings import FindingStore, NewFinding

from .finder_fakes import wanted, wire


def offer(db, sid, kind, fills, key):
    found, _ = FindingStore(db).offer(NewFinding(series_id=sid, kind=kind, pick_key=key, title=key, fills=tuple(fills),
                                                 size_bytes=None, reason="the series is on Notify", payload={}))
    return found.id


def test_a_finding_whose_gap_was_filled_is_retired_and_the_rest_stay(db, series, tmp_path):
    w = wire(db, series, tmp_path, suwayomi=True)
    filled = offer(db, w.series_id, "chapters", ["63", "64"], "chapters:1:dropped")
    part = offer(db, w.series_id, "chapters", ["62.1", "62.2"], "chapters:1:621,622")
    volume = offer(db, w.series_id, "volumes", ["3"], "nyaa:abc")
    w.entries = wanted(w.series_id, w.folder, chapters=["62"], volumes=["3"])
    assert retire_filled(db, w.wanted) == 1
    status = {f.id: f.status for f in FindingStore(db).list(None, w.series_id)}
    assert status == {filled: "gone", part: "waiting", volume: "waiting"}    # 62.1 + 62.2 fill the missing 62


def test_a_series_that_left_the_wanted_list_loses_every_waiting_finding(db, series, tmp_path):
    w = wire(db, series, tmp_path, suwayomi=True)
    offer(db, w.series_id, "chapters", ["63"], "chapters:1:63")
    w.entries = []
    assert retire_filled(db, w.wanted) == 1 and FindingStore(db).list("waiting", w.series_id) == []
