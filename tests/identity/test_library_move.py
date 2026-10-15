"""Move to library (mangalist.library_move): the series moves between two library roots through one cross-root journal
plan and its identity follows DIRECTLY - same series id, link, kind answer, own mode, examined mark, Unbundled setting,
download history - with the same result as a move recognised by the archives; refusals; EXDEV; undo. Synthetic trees
and made-up names only."""

from __future__ import annotations

import errno
import threading
from pathlib import Path

import pytest

from mangalist import library_move, mu_cache
from mangalist.store import LOCK_NAME, RootLock
from mangalist.store import journal as journal_mod
from mangalist.store.findings import FindingStore
from mangalist.store.series import link_key
from mangalist.unbundled import UnbundledChapters

from .conftest import make_archive, scan, sign


@pytest.fixture
def two(db, library, library2):
    """"Manga" (Ongoing-like, default mode) holding three series; "Completed" set to Off and Rename automatically."""
    for name in ("Quiet Harbor", "Lantern Road", "Glass Orchard"):
        make_archive(library / name / f"{name} v01.cbz")
        make_archive(library / name / f"{name} v02.cbz")
    make_archive(library2 / "Paper Moths" / "Paper Moths v01.cbz")
    r1 = db.add_root(str(library), "Manga-Ongoing")
    r2 = db.add_root(str(library2), "Manga-Concluded", auto_mode="off", enforce_naming="automatic")
    scan(db)
    return r1, r2


def _key(root, rel):
    return link_key(Path(root.path).resolve(), rel)


def _dress(db, r1, name, *, mode=None, kind="volumes", mu_id=4242):
    """Give the series every kind of data a move must keep."""
    sid = db.get_series(r1.id, name).id
    mu_cache.save_entry(Path(_key(r1, name)), mu_id, f"{name} (MU)", "https://example.invalid/s", None, mu_confirmed=True)
    db.set_series_kind(r1.id, name, kind)
    if mode:
        FindingStore(db).set_mode([sid], mode)
    UnbundledChapters(db).set([sid], True)
    db.set_setting("examined", [_key(r1, name)])
    with db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status) VALUES"
                    " ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', ?, '{}', 'qbittorrent', 'filed')", (sid,))
    return sid


def _state(db, root, name):
    s = db.get_series(root.id, name)
    with db.connect() as con:
        ledger = [r[0] for r in con.execute("SELECT series_id FROM ledger")]
    link = mu_cache.load_entry(Path(_key(root, name)))
    return {"id": s.id, "status": s.status, "mu_id": s.mu_id, "confirmed": s.mu_confirmed, "kind": s.kind_hint,
            "own_mode": FindingStore(db).own_mode(s.id), "unbundled": UnbundledChapters(db).is_on(s.id),
            "examined": _key(root, name) in db.get_setting("examined", []), "link": link["mu_id"] if link else None,
            "ledger": ledger}


def _move(db, folders, target, **kw):
    check = library_move.check_move(db, [(str(f), Path(f).name) for f in folders], target.id)
    return check, library_move.move_series(db, check, **kw)


def test_the_series_keeps_its_identity_and_a_scan_finds_nothing_new(db, library, library2, two):
    r1, r2 = two
    sid = _dress(db, r1, "Quiet Harbor", mode="auto")
    archives = {a.rel_path.split("/", 1)[1]: a.id for a in db.list_archives(r1.id) if a.rel_path.startswith("Quiet")}
    n_series = len(db.list_series())
    check, res = _move(db, [library / "Quiet Harbor"], r2)
    assert res.status == "moved" and res.message.startswith("Moved 1 series to Manga-Concluded.")
    assert (library2 / "Quiet Harbor" / "Quiet Harbor v01.cbz").is_file() and not (library / "Quiet Harbor").exists()
    assert db.get_series(r1.id, "Quiet Harbor") is None
    assert _state(db, r2, "Quiet Harbor") == {"id": sid, "status": "present", "mu_id": 4242, "confirmed": True,
                                              "kind": "volumes", "own_mode": "auto", "unbundled": True,
                                              "examined": True, "link": 4242, "ledger": [sid]}
    moved = {a.rel_path.split("/", 1)[1]: (a.id, a.series_id) for a in db.list_archives(r2.id)
             if a.rel_path.startswith("Quiet")}
    assert moved == {rel: (aid, sid) for rel, aid in archives.items()}          # the same archive rows, re-pointed
    with db.connect() as con:
        carry = con.execute("SELECT * FROM series_carries").fetchall()
    assert [(c["how"], c["from_series_id"], c["to_series_id"]) for c in carry] == [("journal", sid, sid)]
    rep = scan(db).identity                                                      # both roots again
    assert rep.archives_new == 0 and rep.archives_missing == 0
    assert len(db.list_series()) == n_series and _state(db, r2, "Quiet Harbor")["id"] == sid
    assert not (library / LOCK_NAME).exists() and not (library2 / LOCK_NAME).exists()


def test_the_result_is_the_same_as_a_move_recognised_by_the_archives(db, library, library2, two):
    r1, r2 = two
    _dress(db, r1, "Quiet Harbor", mode="notify")
    _dress(db, r1, "Lantern Road", mode="notify")
    db.set_setting("examined", [_key(r1, "Quiet Harbor"), _key(r1, "Lantern Road")])
    sign(db)                                                    # a hand-made move is recognised by the signatures
    (library / "Lantern Road").rename(library2 / "Lantern Road")                # by hand, then scanned
    scan(db)
    _move(db, [library / "Quiet Harbor"], r2)                                   # through MangaList
    by_hand, by_menu = _state(db, r2, "Lantern Road"), _state(db, r2, "Quiet Harbor")
    for st in (by_hand, by_menu):
        st.pop("id")
        st.pop("ledger")
    assert by_menu == by_hand


def test_a_series_on_the_default_takes_the_target_librarys_mode(db, library, two):
    r1, r2 = two
    sid = db.get_series(r1.id, "Lantern Road").id
    assert FindingStore(db).mode(sid) == "notify"
    check, res = _move(db, [library / "Lantern Road"], r2)
    assert res.ok and FindingStore(db).mode(sid) == "off" and FindingStore(db).own_mode(sid) is None


def test_the_confirmation_names_what_moves_where_and_what_changes(db, library, library2, two):
    r1, r2 = two
    FindingStore(db).set_mode([db.get_series(r1.id, "Glass Orchard").id], "auto")
    check = library_move.check_move(db, [(str(library / n), n) for n in ("Quiet Harbor", "Lantern Road",
                                                                         "Glass Orchard")], r2.id)
    title, text, button = library_move.confirm_text(check)
    assert title == "Move to Manga-Concluded?" and button == "Move 3 series"
    assert text.startswith("Move 3 series from Manga-Ongoing to Manga-Concluded?")
    assert f"Into the folder: {library2} (the top of the library)" in text
    assert ('Automatic downloads: 2 series on "Use the default" will follow Manga-Concluded\'s default: Off (now Notify).'
            " 1 series keeps its own mode.") in text
    assert "Files not named by the scheme: Rename automatically" in text
    assert "Undo: Move to library > Undo last move." in text
    assert "Not moved" not in text
    _t, quiet, _b = library_move.confirm_text(check, show_modes=False)
    assert "Automatic downloads" not in quiet


@pytest.mark.parametrize("status", ["queued", "sent", "downloaded"])
def test_a_download_in_progress_refuses_the_series(db, library, two, status):
    r1, r2 = two
    sid = db.get_series(r1.id, "Quiet Harbor").id
    with db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status) VALUES"
                    " ('2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z', ?, '{}', 'qbittorrent', ?)", (sid, status))
    check = library_move.check_move(db, [(str(library / "Quiet Harbor"), "Quiet Harbor"),
                                         (str(library / "Lantern Road"), "Lantern Road")], r2.id)
    assert [it.title for it in check.items] == ["Lantern Road"]
    assert check.refused[0][0] == "Quiet Harbor" and "a download is in progress" in check.refused[0][1]
    assert "Not moved:" in library_move.confirm_text(check)[1]


def test_other_refusals(db, library, library2, two):
    r1, r2 = two
    make_archive(library2 / "Lantern Road" / "other.cbz")                       # the target name is taken
    (library / "Unscanned Tale").mkdir()
    db.set_exclusions(r2.id, ["Glass Orchard"])
    folders = [(str(library / n), n) for n in ("Lantern Road", "Unscanned Tale", "Glass Orchard")]
    folders.append((str(library2 / "Paper Moths"), "Paper Moths"))
    (library / "Quiet Harbor").rename(library / "Quiet Harbor (gone)")
    folders.append((str(library / "Quiet Harbor"), "Quiet Harbor"))
    check = library_move.check_move(db, folders, r2.id)
    why = dict(check.refused)
    assert not check.items and check.already == ["Paper Moths"]
    assert "already has a folder named 'Lantern Road'" in why["Lantern Road"]
    assert "not scanned" in why["Unscanned Tale"]
    assert "excludes 'Glass Orchard'" in why["Glass Orchard"]
    assert "its folder is missing" in why["Quiet Harbor"]
    assert library_move.move_series(db, check).status == "refused"


def test_a_scan_or_another_instance_holding_a_lock_refuses_the_move(db, library, library2, two):
    from mangalist.scan_lock import scan_lock

    r1, r2 = two
    check = library_move.check_move(db, [(str(library / "Quiet Harbor"), "Quiet Harbor")], r2.id)
    held, release = threading.Event(), threading.Event()

    def scanning():
        with scan_lock(db, what="a test scan"):
            held.set()
            release.wait(10)

    t = threading.Thread(target=scanning)
    t.start()
    held.wait(5)
    try:
        res = library_move.move_series(db, check, scan_wait=0.3)
    finally:
        release.set()
        t.join()
    assert res.status == "refused" and library_move.SCAN_RUNNING in res.message
    other = RootLock(library2, host="the-container")
    other.acquire()
    try:
        res = library_move.move_series(db, check)
    finally:
        other.release()
    assert res.status == "refused" and "Manga-Concluded is being changed by the-container" in res.message
    assert (library / "Quiet Harbor").is_dir() and not (library / LOCK_NAME).exists()
    assert library_move.move_series(db, check).ok                               # free again: it moves


def test_exdev_from_the_system_says_move_it_yourself_and_changes_nothing(db, library, library2, two, monkeypatch):
    r1, r2 = two
    sid = db.get_series(r1.id, "Quiet Harbor").id

    def exdev(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(journal_mod, "rename_noreplace", exdev)
    check, res = _move(db, [library / "Quiet Harbor"], r2)
    assert res.status == "failed" and res.message == library_move.EXDEV_TEXT
    assert "The system refused to move the folder" in res.message
    assert "move it yourself with a file manager" in res.message and "recognises it at the next scan" in res.message
    assert (library / "Quiet Harbor").is_dir() and not (library2 / "Quiet Harbor").exists()
    assert db.get_series(r1.id, "Quiet Harbor").id == sid
    assert library_move.last_move(db) is None                                   # nothing to undo


def test_no_up_front_refusal_for_different_devices_the_move_is_tried(db, library, library2, two, monkeypatch):
    """Owner, 2026-10-15: "MangaList should attempt to move it" - nothing compares the two sides' devices first."""
    r1, r2 = two
    tried = []
    real = journal_mod.rename_noreplace
    monkeypatch.setattr(journal_mod, "rename_noreplace", lambda src, dst: tried.append(dst) or real(src, dst))
    check, res = _move(db, [library / "Quiet Harbor"], r2)
    assert tried == [str(library2 / "Quiet Harbor")] and res.ok


def test_any_other_refusal_by_the_system_says_move_it_yourself(db, library, library2, two, monkeypatch):
    r1, r2 = two

    def refuse(src, dst):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(journal_mod, "rename_noreplace", refuse)
    check, res = _move(db, [library / "Quiet Harbor"], r2)
    assert res.status == "failed" and not res.moved
    assert res.message.startswith("The system refused to move Quiet Harbor (") and "Permission denied" in res.message
    assert "Nothing was moved. Please move it yourself with a file manager" in res.message
    assert (library / "Quiet Harbor").is_dir() and library_move.last_move(db) is None


def _hold_replacements(db, sid, status="held"):
    with db.connect() as con:
        con.execute("INSERT INTO replacements (download_id, series_id, status, created_at, updated_at) VALUES"
                    " (?, ?, ?, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z')", (900 + sid, sid, status))


def test_replaced_chapters_held_in_the_holding_folder_refuse_the_move(db, library, two):
    """Owner, 2026-10-15: "Refuse until emptied" (it only warned before)."""
    r1, r2 = two
    _hold_replacements(db, db.get_series(r1.id, "Quiet Harbor").id)
    check = library_move.check_move(db, [(str(library / "Quiet Harbor"), "Quiet Harbor"),
                                         (str(library / "Lantern Road"), "Lantern Road")], r2.id)
    assert [it.title for it in check.items] == ["Lantern Road"]
    assert check.refused == [("Quiet Harbor", library_move.HELD_REASON)]
    assert "empty or restore them first" in library_move.HELD_REASON
    text = library_move.confirm_text(check)[1]
    assert "Not moved:" in text and "holding folder" in text and "Restore can no longer" not in text
    _hold_replacements(db, db.get_series(r1.id, "Lantern Road").id)            # held after the confirmation
    res = library_move.move_series(db, check)
    assert res.status == "refused" and "Lantern Road" in res.message and (library / "Lantern Road").is_dir()


def test_restored_or_purged_replacements_do_not_refuse(db, library, two):
    r1, r2 = two
    _hold_replacements(db, db.get_series(r1.id, "Quiet Harbor").id, "purged")
    check = library_move.check_move(db, [(str(library / "Quiet Harbor"), "Quiet Harbor")], r2.id)
    assert [it.title for it in check.items] == ["Quiet Harbor"] and not check.refused


def test_the_owner_can_choose_a_folder_inside_the_target_library(db, library, library2, two):
    """Owner, 2026-10-15: "OK default, user settable location" - e.g. into a franchise parent."""
    r1, r2 = two
    (library2 / "Harbor Saga").mkdir()
    (library2 / ".hidden").mkdir()
    assert library_move.place_choices(db, r2.id) == ["Harbor Saga", "Paper Moths"]
    sid = _dress(db, r1, "Quiet Harbor")
    check = library_move.check_move(db, [(str(library / "Quiet Harbor"), "Quiet Harbor")], r2.id, into="Harbor Saga")
    assert check.items[0].target == str(library2 / "Harbor Saga" / "Quiet Harbor")
    assert check.items[0].new_rel == "Harbor Saga/Quiet Harbor"
    title, text, _b = library_move.confirm_text(check)
    assert text.startswith("Move 1 series from Manga-Ongoing to Manga-Concluded / Harbor Saga?")
    assert f"Into the folder: {library2 / 'Harbor Saga'}" in text and "(the top of the library)" not in text
    res = library_move.move_series(db, check)
    assert res.ok and (library2 / "Harbor Saga" / "Quiet Harbor").is_dir() and not (library / "Quiet Harbor").exists()
    moved = db.get_series(r2.id, "Harbor Saga/Quiet Harbor")
    assert moved is not None and moved.id == sid
    scan(db)                                                                    # read as a subseries of the parent
    assert db.get_series(r2.id, "Harbor Saga/Quiet Harbor").id == sid
    undo = library_move.undo_move(db, library_move.last_move(db).plan_id)
    assert undo.status == "undone" and (library / "Quiet Harbor").is_dir() and (library2 / "Harbor Saga").is_dir()


def test_a_chosen_folder_is_checked(db, library, library2, two):
    r1, r2 = two
    folders = [(str(library / "Quiet Harbor"), "Quiet Harbor")]
    for into, why in (("Nowhere", "has no folder named 'Nowhere'"), ("a/b", "is not a folder at the top"),
                      ("..", "is not a folder at the top")):
        check = library_move.check_move(db, folders, r2.id, into=into)
        assert not check.items and why in check.refused[0][1], into
    (library2 / "Hidden Parent").mkdir()
    db.set_exclusions(r2.id, ["Hidden Parent"])
    check = library_move.check_move(db, folders, r2.id, into="Hidden Parent")
    assert not check.items and "excludes 'Hidden Parent'" in check.refused[0][1]
    make_archive(library2 / "Paper Moths" / "Quiet Harbor" / "x.cbz")         # taken inside the parent
    check = library_move.check_move(db, folders, r2.id, into="Paper Moths")
    assert "Manga-Concluded / Paper Moths already has a folder named 'Quiet Harbor'" in check.refused[0][1]


def test_into_a_folder_refuses_a_franchise_parent_and_a_unit_name(db, library, library2, two):
    r1, r2 = two
    make_archive(library / "Moth Cycle" / "Moth Cycle v01.cbz")                # a parent with its own files ...
    make_archive(library / "Moth Cycle" / "Moth Cycle Side" / "Moth Cycle Side v01.cbz")     # ... and a subseries
    make_archive(library / "Season 2" / "Season 2 v01.cbz")
    scan(db)
    (library2 / "Parent Shelf").mkdir()
    check = library_move.check_move(db, [(str(library / "Moth Cycle"), "Moth Cycle"),
                                         (str(library / "Season 2"), "Season 2")], r2.id, into="Parent Shelf")
    why = dict(check.refused)
    assert not check.items
    assert "holds subseries of its own" in why["Moth Cycle"] and "reads as a part of 'Parent Shelf'" in why["Season 2"]
    top = library_move.check_move(db, [(str(library / "Season 2"), "Season 2")], r2.id)
    assert [it.title for it in top.items] == ["Season 2"]                       # the top of the library: fine


def test_undo_puts_the_series_back_with_its_identity(db, library, library2, two):
    r1, r2 = two
    sid = _dress(db, r1, "Quiet Harbor")
    before = _state(db, r1, "Quiet Harbor")
    _move(db, [library / "Quiet Harbor", library / "Lantern Road"], r2)
    last = library_move.last_move(db)
    assert last.count == 2 and last.label == "Undo last move (2 series back to Manga-Ongoing)"
    res = library_move.undo_move(db, last.plan_id)
    assert res.status == "undone" and res.message == "Moved 2 series back."
    assert sorted(Path(p).name for p, _src in res.moved) == ["Lantern Road", "Quiet Harbor"]
    assert _state(db, r1, "Quiet Harbor") == before and before["id"] == sid
    assert library_move.last_move(db) is None
    rep = scan(db).identity
    assert rep.archives_new == 0 and rep.archives_missing == 0


def test_undo_is_refused_while_a_download_files_into_the_new_folder(db, library, two):
    r1, r2 = two
    sid = db.get_series(r1.id, "Quiet Harbor").id
    _move(db, [library / "Quiet Harbor"], r2)
    with db.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, series_id, request, tool, status) VALUES"
                    " ('2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z', ?, '{}', 'suwayomi', 'sent')", (sid,))
    res = library_move.undo_move(db, library_move.last_move(db).plan_id)
    assert res.status == "refused" and "a download is in progress" in res.message
