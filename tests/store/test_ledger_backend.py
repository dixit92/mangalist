"""Schema 10 (transition audit B2, 2026-10-24): a chapter record says which program it belongs to - Suwayomi's and the
built-in extension host's chapter ids both start at 1 - and the 9 -> 10 upgrade classifies the existing records (the
owner's box: records before the switch are Suwayomi's, 2063-2065 the host's). Made-up names and ids."""

from __future__ import annotations

import json
import sqlite3

import pytest

from mangalist import store
from mangalist.downloads.contracts import (
    CHAPTER_BACKEND_EXTENSIONS as EXT,
    CHAPTER_BACKEND_SUWAYOMI as SUW,
    TOOL_SUWAYOMI,
    DownloadStatus as S,
    SuwayomiChapter,
    SuwayomiManga,
    SuwayomiSource,
)
from mangalist.store import schema
from mangalist.store.downloads import DownloadLedger, SuwayomiSettings

SOURCE = SuwayomiSource(id="2499283573021220255", name="MangaDex", display_name="MangaDex (EN)", lang="en")


def chapter(cid: int, number: str) -> SuwayomiChapter:
    return SuwayomiChapter(id=cid, manga_id=1, name=f"Ch.{number}", number=number, scanlator=None, url=f"/c/{cid}")


def _schema_9(path):
    con = sqlite3.connect(path)
    for version, script in schema.MIGRATIONS:
        if version > 9:
            break
        con.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")
    con.execute("INSERT INTO roots (id, name, path, created_at, updated_at) VALUES (1, 'M', '/x', 't', 't')")
    for sid in (85, 114, 592, 600):
        con.execute("INSERT INTO series (id, root_id, rel_path, n_archives, status, first_seen_at, last_seen_at)"
                    " VALUES (?, 1, ?, 1, 'present', 't', 't')", (sid, f"Series {sid}"))
    return con


def _record(con, rid, sid, manga_id, ref, status="removed", created="2026-10-23T05:00:00+00:00", tool="suwayomi"):
    req = {"title": "x", "wanted_chapters": ["1"]} if manga_id is None else \
        {"title": "x", "wanted_chapters": ["1"], "manga_id": manga_id}
    con.execute("INSERT INTO ledger (id, created_at, updated_at, series_id, request, tool, status, external_ref)"
                " VALUES (?,?,?,?,?,?,?,?)", (rid, created, created, sid, json.dumps(req), tool, status, str(ref)))


def _choice(con, prefix, sid, manga_id):
    con.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (f"{prefix}{sid}", json.dumps({"manga_id": manga_id})))


def test_the_upgrade_classifies_the_existing_chapter_records_like_the_owners(tmp_path):
    path = tmp_path / "old.db"
    con = _schema_9(path)
    # the owner's choices: Suwayomi's and the host's manga ids of the same series differ
    _choice(con, "suwayomi.series.", 114, 3860)
    _choice(con, "extensions.series.", 114, 114)
    _choice(con, "suwayomi.series.", 85, 2435)
    _choice(con, "extensions.series.", 85, 85)
    _choice(con, "suwayomi.series.", 592, 3818)
    _record(con, 2050, 114, 3860, 13986)                                            # Suwayomi's, filed and removed
    _record(con, 2061, 592, 3818, 14000)
    _record(con, 2062, None, None, "ab" * 20, tool="qbittorrent")                   # a torrent: no program
    _record(con, 2063, 85, 85, 9326, created="2026-10-24T08:32:51+00:00")           # the host's
    _record(con, 2064, 85, 85, 9327, created="2026-10-24T08:32:51+00:00")
    _record(con, 2065, 114, 114, 5809, status="sent", created="2026-10-24T08:33:59+00:00")
    _record(con, 2066, 600, 7, 5810, status="sent", created="2026-10-24T09:00:00+00:00")     # no evidence: after both
    _record(con, 1990, 600, 99, 300, created="2026-10-01T00:00:00+00:00")                    # no evidence: early
    con.commit()
    con.close()

    st = store.Store(path, import_legacy=False)
    assert st.schema_version() == schema.SCHEMA_VERSION >= 10
    with st.connect() as c:
        got = {r["id"]: r["backend"] for r in c.execute("SELECT id, backend FROM ledger")}
    assert got == {1990: SUW, 2050: SUW, 2061: SUW, 2062: None, 2063: EXT, 2064: EXT, 2065: EXT, 2066: EXT}
    rec = DownloadLedger(st, tool=None).get(2065)
    assert rec.backend == EXT and rec.is_chapters and rec.status == S.SENT


def test_a_database_without_the_built_in_extensions_keeps_every_record_suwayomis(tmp_path):
    path = tmp_path / "old.db"
    con = _schema_9(path)
    _choice(con, "suwayomi.series.", 114, 3860)
    _record(con, 1, 114, 3860, 5)
    _record(con, 2, 114, 777, 6)                         # the choice changed since: no evidence, still Suwayomi's
    con.commit()
    con.close()
    st = store.Store(path, import_legacy=False)
    with st.connect() as c:
        assert {r[0]: r[1] for r in c.execute("SELECT id, backend FROM ledger")} == {1: SUW, 2: SUW}


@pytest.fixture
def lib(db, tmp_path):
    root = db.add_root(str(tmp_path / "lib"), "Lib")
    db.record_scan(root.id, tmp_path / "lib", [store.SeriesSeen(name, None, 0) for name in ("A", "B")])
    return {s.rel_path: s.id for s in db.list_series()}


@pytest.fixture
def chapters(db):
    return DownloadLedger(db, tool=TOOL_SUWAYOMI)


def send(chapters, sid, ids_numbers, backend, batch="b"):
    return chapters.create_chapters(sid, [chapter(i, n) for i, n in ids_numbers], source=SOURCE,
                                    manga=SuwayomiManga(id=1, title="T", url="/m/1", source_id=SOURCE.id),
                                    target_dir="/lib/A", batch=batch, backend=backend)


def test_the_same_chapter_id_of_the_other_program_is_another_chapter(chapters, lib):
    made, _ = send(chapters, lib["A"], [(5, "166")], SUW)
    assert made[0].backend == SUW
    # the host's chapter 5 of the same series is another chapter: sent (before schema 10: "ch 297: already sent")
    made, skipped = send(chapters, lib["A"], [(5, "297")], EXT)
    assert [r.wanted_chapters for r in made] == [("297",)] and not skipped and made[0].backend == EXT
    # the same NUMBER from the other program is still in hand, whichever program has it
    made, skipped = send(chapters, lib["A"], [(9, "166")], EXT)
    assert not made and skipped == ["ch 166: already sent"]
    # another series: the same program's id is on its way for the other folder ...
    made, skipped = send(chapters, lib["B"], [(5, "1")], EXT, batch="c")
    assert not made and skipped == ["ch 1: already on its way for another folder"]
    # ... the other program's same id is another chapter (before schema 10: skipped as well)
    send(chapters, lib["A"], [(8, "300")], EXT, batch="e")
    made, skipped = send(chapters, lib["B"], [(8, "2")], SUW, batch="d")
    assert [r.wanted_chapters for r in made] == [("2",)] and not skipped


def test_a_record_of_unknown_program_counts_for_both(db, chapters, lib):
    send(chapters, lib["A"], [(5, "10")], SUW)
    with db.connect() as con:
        con.execute("UPDATE ledger SET backend = NULL")               # an older build's record
    for backend in (SUW, EXT):
        made, skipped = send(chapters, lib["A"], [(5, "11")], backend)
        assert not made and skipped == ["ch 11: already sent"]


def test_active_by_program_and_the_stuck_mark(chapters, lib):
    a, _ = send(chapters, lib["A"], [(5, "1")], SUW)
    b, _ = send(chapters, lib["A"], [(6, "2")], EXT)
    assert [r.id for r in chapters.active((EXT,))] == [b[0].id]
    assert [r.id for r in chapters.active((SUW, ""))] == [a[0].id]
    assert chapters.set_stuck(b[0].id, True) and chapters.get(b[0].id).stuck
    assert not chapters.set_stuck(b[0].id, True)
    assert chapters.set_stuck(b[0].id, False) and not chapters.get(b[0].id).stuck
    with pytest.raises(Exception):
        send(chapters, lib["A"], [(7, "3")], "nope")


def test_series_choices_go_under_the_program_that_answered(db, lib):
    settings = SuwayomiSettings(db)
    assert settings.backend() == SUW
    settings.set_series_choice(lib["A"], backend=EXT, manga_id=73, source_id="1", how="confirmed")
    assert settings.series_choice(lib["A"]) == {}                                    # Suwayomi's untouched
    assert settings.series_choice(lib["A"], EXT)["manga_id"] == 73
    db.set_meta("extensions.host_instance", "db-1")
    settings.set_series_choice(lib["A"], backend=EXT, manga_id=74, carried_from={"x": 1})
    assert settings.series_choice(lib["A"], EXT)["host_instance"] == "db-1"
    settings.set_series_choice(lib["A"], backend=EXT, manga_id=75)                  # chosen on the host now
    assert "carried_from" not in settings.series_choice(lib["A"], EXT)
