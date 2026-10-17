"""Merge into... (mangalist.series_merge): the files and sub-folders of one series folder moved into another through one
journal plan - within a library folder or across two - never replacing a name; the emptied folder removed and its
series folded into the kept one (which keeps its id, link and history); refusals; EXDEV; undo with the identity back.
Synthetic trees and made-up names only."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from mangalist import mu_cache, series_merge
from mangalist.store import LOCK_NAME
from mangalist.store import journal as journal_mod
from mangalist.store.findings import FindingStore
from mangalist.store.series import link_key

from .conftest import make_archive, scan


def _key(root, rel):
    return link_key(Path(root.path).resolve(), rel)


@pytest.fixture
def two(db, library, library2):
    """Ongoing: "Summoned Partner" v01-v02 + Extras; Concluded: "Summoned Partner (EN)" v02 (a copy) + v03."""
    make_archive(library / "Summoned Partner" / "Summoned Partner v01.cbz")
    make_archive(library / "Summoned Partner" / "Summoned Partner v02.cbz")
    make_archive(library / "Summoned Partner" / "Extras" / "Summoned Partner Art.cbz")
    make_archive(library2 / "Summoned Partner (EN)" / "Summoned Partner v02.cbz", seed="other v02 copy")
    make_archive(library2 / "Summoned Partner (EN)" / "Summoned Partner v03.cbz")
    r1 = db.add_root(str(library), "Manga-Ongoing")
    r2 = db.add_root(str(library2), "Manga-Concluded")
    scan(db)
    return r1, r2


@pytest.fixture
def apart(db, library, library2):
    """As *two*, without a name both folders hold."""
    make_archive(library / "Summoned Partner" / "Summoned Partner v01.cbz")
    make_archive(library / "Summoned Partner" / "Summoned Partner v02.cbz")
    make_archive(library / "Summoned Partner" / "Extras" / "Summoned Partner Art.cbz")
    make_archive(library2 / "Summoned Partner (EN)" / "Summoned Partner v03.cbz")
    r1 = db.add_root(str(library), "Manga-Ongoing")
    r2 = db.add_root(str(library2), "Manga-Concluded")
    scan(db)
    return r1, r2


def _dress(db, root, rel, mu_id=4242, *, kind=None, mode=None, ledger="filed"):
    sid = db.get_series(root.id, rel).id
    mu_cache.save_entry(Path(_key(root, rel)), mu_id, f"{rel} (MU)", "https://example.invalid/s", None, mu_confirmed=True)
    if kind:
        db.set_series_kind(root.id, rel, kind)
    if mode:
        FindingStore(db).set_mode([sid], mode)
    if ledger:
        with db.connect() as con:
            con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status) VALUES"
                        " ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', ?, '{}', 'qbittorrent', ?)", (sid, ledger))
    db.sync_identity(_key(root, rel))
    return sid


def _ledger(db):
    with db.connect() as con:
        return sorted(r[0] for r in con.execute("SELECT series_id FROM ledger"))


def test_merge_across_libraries_moves_everything_but_a_name_the_kept_folder_has(db, library, library2, two):
    r1, r2 = two
    kept_id = _dress(db, r2, "Summoned Partner (EN)", kind="volumes", mode="auto")
    gone_id = _dress(db, r1, "Summoned Partner", mu_id=4242)
    check = series_merge.check_merge(db, library / "Summoned Partner", library2 / "Summoned Partner (EN)")
    assert check.ok and check.across
    assert sorted(Path(s).name for s, _d in check.moves) == ["Extras", "Summoned Partner v01.cbz"]
    assert check.left == [("Summoned Partner v02.cbz", "the folder kept already has this name (nothing is replaced)")]
    title, text, button = series_merge.confirm_text(check)
    assert button == "Merge 2 items" and "Manga-Ongoing / Summoned Partner" in text and "Left where they are:" in text
    res = series_merge.merge_folders(db, check)
    assert res.status == "merged" and not res.removed
    assert "left in Summoned Partner: Summoned Partner v02.cbz" in res.message and series_merge.UNDO_HINT in res.message
    kept = library2 / "Summoned Partner (EN)"
    assert (kept / "Summoned Partner v01.cbz").is_file() and (kept / "Extras" / "Summoned Partner Art.cbz").is_file()
    assert (library / "Summoned Partner" / "Summoned Partner v02.cbz").is_file()        # never replaced
    # the folder still holds a file: it stays its own series; the moved archives belong to the kept one
    assert db.get_series(r1.id, "Summoned Partner").id == gone_id
    moved = {a.rel_path: a.series_id for a in db.list_archives(r2.id)}
    assert moved["Summoned Partner (EN)/Summoned Partner v01.cbz"] == kept_id
    assert moved["Summoned Partner (EN)/Extras/Summoned Partner Art.cbz"] == kept_id
    assert not (library / LOCK_NAME).exists() and not (library2 / LOCK_NAME).exists()
    rep = scan(db).identity
    assert rep.archives_new == 0 and rep.archives_missing == 0


def test_a_merge_that_empties_the_folder_removes_it_and_folds_its_series_into_the_kept_one(db, library, library2, apart):
    r1, r2 = apart
    kept_id = _dress(db, r2, "Summoned Partner (EN)", mode="auto", ledger="filed")
    gone_id = _dress(db, r1, "Summoned Partner", mu_id=4242, kind="volumes", ledger="completed")
    db.set_setting("examined", [_key(r1, "Summoned Partner")])
    check = series_merge.check_merge(db, library / "Summoned Partner", library2 / "Summoned Partner (EN)")
    res = series_merge.merge_folders(db, check)
    assert res.status == "merged" and res.removed and "the emptied folder was removed" in res.message
    assert not (library / "Summoned Partner").exists()
    kept = db.get_series(r2.id, "Summoned Partner (EN)")
    assert kept.id == kept_id and kept.kind_hint == "volumes" and kept.mu_id == 4242      # the answer it lacked
    assert FindingStore(db).own_mode(kept_id) == "auto"                                   # its own settings stay
    assert db.get_series(r1.id, "Summoned Partner") is None
    assert _ledger(db) == [kept_id, kept_id]                                              # the history joined it
    assert mu_cache.load_entry(Path(_key(r1, "Summoned Partner"))) is None                # its link: the kept one's
    assert db.get_setting("examined", []) == [_key(r2, "Summoned Partner (EN)")]
    with db.connect() as con:
        carry = con.execute("SELECT how, from_series_id, to_series_id, kept FROM series_carries").fetchall()
    assert [(c["how"], c["from_series_id"], c["to_series_id"], c["kept"]) for c in carry][-1] == (
        "merge", gone_id, kept_id, '["link"]')
    rep = scan(db).identity
    assert rep.archives_new == 0 and rep.archives_missing == 0
    assert all(s.status == "present" for s in db.list_series())                         # nothing in Missing series


def test_undo_puts_the_files_and_the_folded_series_back(db, library, library2, apart):
    r1, r2 = apart
    kept_id = _dress(db, r2, "Summoned Partner (EN)", ledger=None)
    gone_id = _dress(db, r1, "Summoned Partner", mu_id=4242, kind="chapters", ledger="completed")
    db.set_setting("examined", [_key(r1, "Summoned Partner")])
    series_merge.merge_folders(db, series_merge.check_merge(db, library / "Summoned Partner",
                                                            library2 / "Summoned Partner (EN)"))
    last = series_merge.last_merge(db)
    assert last.count == 3 and last.label == "Undo last merge (3 items back to Summoned Partner)"
    res = series_merge.undo_merge(db, last.plan_id)
    assert res.status == "undone" and res.message == "Moved 3 items back to Manga-Ongoing / Summoned Partner."
    assert (library / "Summoned Partner" / "Extras" / "Summoned Partner Art.cbz").is_file()
    assert sorted(p.name for p in (library2 / "Summoned Partner (EN)").iterdir()) == ["Summoned Partner v03.cbz"]
    back = db.get_series(r1.id, "Summoned Partner")
    assert back.id == gone_id and back.kind_hint == "chapters" and back.mu_id == 4242 and back.status == "present"
    assert db.get_series(r2.id, "Summoned Partner (EN)").kind_hint is None
    assert mu_cache.load_entry(Path(_key(r1, "Summoned Partner")))["mu_id"] == 4242
    assert _ledger(db) == [gone_id] and _key(r1, "Summoned Partner") in db.get_setting("examined", [])
    assert {a.series_id for a in db.list_archives(r1.id)} == {gone_id}
    assert series_merge.last_merge(db) is None
    rep = scan(db).identity
    assert rep.archives_new == 0 and rep.archives_missing == 0 and kept_id != gone_id


def test_a_nested_copy_merges_into_its_parent_within_one_library(db, library, two):
    r1, _r2 = two
    make_archive(library / "Elder Knight Tale" / "Elder Knight Tale v01.cbz")
    make_archive(library / "Elder Knight Tale" / "Elder Knight Tale" / "Elder Knight Tale v02.cbz")
    scan(db)
    parent = db.get_series(r1.id, "Elder Knight Tale").id
    outer, inner = library / "Elder Knight Tale", library / "Elder Knight Tale" / "Elder Knight Tale"
    refused = series_merge.check_merge(db, outer, inner)
    assert not refused.ok and "lies inside the one merged away" in refused.refused
    check = series_merge.check_merge(db, inner, outer)
    assert check.ok and not check.across
    res = series_merge.merge_folders(db, check)
    assert res.status == "merged" and res.removed
    assert sorted(p.name for p in outer.iterdir()) == ["Elder Knight Tale v01.cbz", "Elder Knight Tale v02.cbz"]
    assert db.get_series(r1.id, "Elder Knight Tale/Elder Knight Tale") is None
    assert db.get_series(r1.id, "Elder Knight Tale").id == parent


@pytest.mark.parametrize("status", ["queued", "sent", "downloaded"])
def test_a_download_in_progress_refuses_the_merge(db, library, library2, two, status):
    r1, r2 = two
    _dress(db, r1, "Summoned Partner", ledger=status)
    check = series_merge.check_merge(db, library / "Summoned Partner", library2 / "Summoned Partner (EN)")
    assert not check.ok and "a download is in progress" in check.refused
    res = series_merge.merge_folders(db, check)
    assert res.status == "refused" and "a download is in progress" in res.message
    assert (library / "Summoned Partner" / "Summoned Partner v01.cbz").is_file()


def test_held_replaced_chapters_and_other_refusals(db, library, library2, two):
    r1, r2 = two
    sid = db.get_series(r1.id, "Summoned Partner").id
    with db.connect() as con:
        con.execute("INSERT INTO replacements (download_id, series_id, status, created_at, updated_at) VALUES"
                    " (901, ?, 'held', '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')", (sid,))
    check = series_merge.check_merge(db, library / "Summoned Partner", library2 / "Summoned Partner (EN)")
    assert "held in the holding folder" in check.refused
    (library / "Unscanned").mkdir()
    assert "not scanned" in series_merge.check_merge(db, library / "Unscanned", library2 / "Summoned Partner (EN)").refused
    same = library2 / "Summoned Partner (EN)"
    assert "the same" in series_merge.check_merge(db, same, same).refused


def test_exdev_says_move_them_yourself_and_changes_nothing(db, library, library2, two, monkeypatch):
    r1, _r2 = two
    sid = db.get_series(r1.id, "Summoned Partner").id

    def exdev(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(journal_mod, "rename_noreplace", exdev)
    check = series_merge.check_merge(db, library / "Summoned Partner", library2 / "Summoned Partner (EN)")
    res = series_merge.merge_folders(db, check)
    assert res.status == "failed" and res.message == series_merge.EXDEV_TEXT
    assert "move them yourself with a file manager" in res.message
    assert (library / "Summoned Partner" / "Summoned Partner v01.cbz").is_file()
    assert db.get_series(r1.id, "Summoned Partner").id == sid and series_merge.last_merge(db) is None


def test_a_running_scan_refuses_the_merge(db, library, library2, two):
    import threading

    from mangalist.scan_lock import scan_lock

    check = series_merge.check_merge(db, library / "Summoned Partner", library2 / "Summoned Partner (EN)")
    held, release = threading.Event(), threading.Event()

    def scanning():
        with scan_lock(db, what="a test scan"):
            held.set()
            release.wait(10)

    t = threading.Thread(target=scanning)
    t.start()
    held.wait(5)
    try:
        res = series_merge.merge_folders(db, check, scan_wait=0.3)
    finally:
        release.set()
        t.join()
    assert res.status == "refused" and "A library scan is running" in res.message
    assert (library / "Summoned Partner" / "Summoned Partner v01.cbz").is_file()
