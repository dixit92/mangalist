"""The automatic pass looks again only at series that changed since a pass found nothing to rename in them (selective
scanning, Stage 0: it dry-ran every series of every automatic root after every headless scan, about 50 s, renaming
nothing). A made-up library, the FAKE namer."""

from __future__ import annotations

from mangalist.renamer import Renamer, record_rescan

from .conftest import fmd2, make_library, payload, rescan
from .fakes import FakeNamer


def _renamer(db, hooks):
    r = Renamer(db, namer=FakeNamer(), title_for=lambda *a: None, rescan=hooks.rescan,
                request_scans=hooks.request_scans)
    looked = []
    real = r.dry_run

    def dry_run(root_ids=None, series_ids=None, **kw):
        looked.append(sorted(series_ids) if series_ids is not None else None)
        return real(root_ids=root_ids, series_ids=series_ids, **kw)

    r.dry_run = dry_run
    return r, looked


def test_only_changed_series_are_looked_at_again(db, library, hooks):
    root = make_library(db, library, {"Series H": {fmd2(1, "0001", "One", "G"): payload("h1")},
                                      "Series J": {"Ch. 0001.00 (One) [G].cbz": payload("j1")}})
    root.enforce_naming = "automatic"
    db.update_root(root)
    Renamer(db, namer=FakeNamer()).mark_guided([root.id])
    h, j = db.get_series(root.id, "Series H").id, db.get_series(root.id, "Series J").id
    r, looked = _renamer(db, hooks)

    first = r.automatic_pass()
    assert first.renamed == 1 and looked == [sorted([h, j])] and first.unchanged == 0
    second = r.automatic_pass()                         # Series H was renamed (and rescanned): looked at once more
    assert second.renamed == 0 and looked[-1] == [h] and second.unchanged == 1
    third = r.automatic_pass()                          # nothing changed anywhere: no dry run at all
    assert third.renamed == 0 and len(looked) == 2 and third.unchanged == 2
    assert "nothing to rename" in third.summary()

    (library / "Series J" / fmd2(2, "0002", "Two", "G")).write_bytes(payload("j2"))     # a file filed into Series J
    rescan(db)
    fourth = r.automatic_pass()
    assert fourth.renamed == 1 and looked[-1] == [j]
    assert sorted(p.name for p in (library / "Series J").iterdir()) == ["Ch. 0001.00 (One) [G].cbz",
                                                                         "Ch. 0002.00 (Two) [G].cbz"]


def test_a_changed_naming_input_looks_at_every_series_again(db, library, hooks):
    root = make_library(db, library, {"Series J": {"Ch. 0001.00 (One) [G].cbz": payload("j1")},
                                      "Series K": {"Ch. 0001.00 (Uno) [G].cbz": payload("k1")}})
    root.enforce_naming = "automatic"
    db.update_root(root)
    Renamer(db, namer=FakeNamer()).mark_guided([root.id])
    r, looked = _renamer(db, hooks)
    r.automatic_pass()
    r.automatic_pass()
    assert len(looked) == 1
    from mangalist.renamer import set_windows_server

    set_windows_server(db, "NAS")                       # the length rule changes every name's limit
    r.automatic_pass()
    assert len(looked) == 2 and len(looked[-1]) == 2


def test_the_default_rescan_after_a_batch_reads_only_the_series_folders(db, library, monkeypatch):
    from mangalist import scanner

    root = make_library(db, library, {"Series H": {fmd2(1, "0001"): payload("h1")},
                                      "Series J": {fmd2(1, "0001"): payload("j1")}})
    (library / "Series J" / fmd2(1, "0001")).unlink()                   # gone, but Series J is not named
    calls = []
    real = scanner.scan_and_record_library
    monkeypatch.setattr(scanner, "scan_and_record_library",
                        lambda roots, db_, **kw: calls.append(kw.get("scopes")) or real(roots, db_, **kw))
    note = record_rescan(db, [root.id], [db.get_series(root.id, "Series H").id])
    assert calls == [{root.id: {"Series H"}}] and note == "rescan of 1 folder(s) recorded"
    assert db.get_series(root.id, "Series J").n_archives == 1                # not read
    assert record_rescan(db, [root.id]) == "rescan of 1 root(s) recorded"   # no series named: the root
    assert db.get_series(root.id, "Series J").n_archives == 0
