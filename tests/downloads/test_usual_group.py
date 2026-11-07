"""A chapter from another group is replaced by the series' usual group's copy when that is released (owner, 2026-11-06:
"Download and if the usual group releases, replace the downloaded chapter."; mangalist.downloads.usual_group): which
sends are provisional, the watch and its bounds, the chapter check sending the usual copy, the filing that holds the
old copy first (restorable), one form per chapter, and what the owner is told. A fake Suwayomi, a temporary library,
made-up names."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from mangalist import upgrades
from mangalist.downloads import chapter_check as cc
from mangalist.downloads import usual_group as ug
from mangalist.downloads.chapter_arrivals import run_chapter_arrivals
from mangalist.downloads.chapters import HOW_MANGADEX, MangaMatch, default_group, send_chapters
from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.gui.volumes_target import chapter_status_text
from mangalist.store.findings import FindingStore
from mangalist.store.replacements import ReplacementStore

from .fakes import data, scan
from .finder_fakes import wire
from .suwayomi_fakes import MANGA, MANGADEX, FakeNamer, chapter

USUAL = "Alder + Birch Scans"           # a joint group, as in the live case
OTHER = "Cedar Scans"


def chapter_file(folder, number: int, group: str = USUAL) -> None:
    n = f"{number:04d}"
    (folder / f"{n} [Ch. {n} - Title {number} [{group}]].cbz").write_bytes(data(f"c{number}"))


@pytest.fixture
def lib(db, tmp_path):
    """'Made Up Series' holding chapters 1-3 by the usual group, on Auto, its MangaDex manga saved; ch 4 is out only
    from another group."""
    root = tmp_path / "library" / "Manga"
    folder = root / "Made Up Series"
    folder.mkdir(parents=True)
    for n in (1, 2, 3):
        chapter_file(folder, n)
    r = db.add_root(str(root), "Manga")
    scan(db)
    sid = db.get_series(r.id, "Made Up Series").id
    w = wire(db, (sid, folder), tmp_path, suwayomi=True)
    w.suwayomi.chapter_list = [chapter(101, "1", USUAL), chapter(102, "2", USUAL), chapter(103, "3", USUAL),
                               chapter(204, "4", OTHER)]
    w.backend.suwayomi.set_series_choice(sid, source_id=MANGADEX.id, manga_id=MANGA.id, manga_title=MANGA.title,
                                         how="confirmed")
    FindingStore(db).set_mode([sid], "auto")
    upgrades.set_holding_folder(db, str(tmp_path / "appdata" / "replaced"))
    w.downloads = tmp_path / "suwayomi"
    return w


def check(db, w):
    return cc.run_chapter_check(db, backend=w.backend, pause=0)


def arrive(w, *ids):
    for cid in ids:
        w.suwayomi.finish(cid, data=data(f"chapter {cid}"))
    report = run_chapter_arrivals(w.suwayomi, w.backend.chapter_ledger, download_dir=str(w.downloads),
                                  namer=FakeNamer())
    scan(w.backend.db)
    return report


def records(w):
    return {r.info_hash: r for r in w.backend.chapter_ledger.all_records()}


def files(w):
    return sorted(p.name for p in w.folder.iterdir())


# --- the usual group ---------------------------------------------------------------------------------------------

def test_the_usual_group_is_the_owners_choice_else_the_folders():
    held = {D(1): [USUAL], D(2): [USUAL], D(3): [USUAL]}
    assert ug.usual_of(held, [D(4)]) == USUAL
    assert ug.usual_of(held, [D(4)], stored="Elm Scans") == "Elm Scans"           # the owner's own choice
    assert ug.usual_of({D(1): [], D(2): []}, [D(3)]) is None                      # no file names a group
    assert ug.usual_of({}, [D(1)]) is None
    # the series changed groups: its nearest chapters tell
    assert ug.usual_of({D(1): [OTHER], D(2): [OTHER], D(8): [USUAL], D(9): [USUAL]}, [D(10)]) == USUAL
    # default_group carries it whatever it picks: "the only group with these chapters" is not the usual one
    choice = default_group({OTHER: 1}, held, [D(4)])
    assert (choice.group, choice.reason, choice.usual) == (OTHER, "the only group with these chapters", USUAL)
    stored = default_group({OTHER: 1}, held, [D(4)], stored=OTHER)
    assert (stored.group, stored.reason, stored.usual) == (OTHER, "your choice for this series", OTHER)


def test_a_joint_group_is_the_same_whatever_joins_it_and_where_the_scheme_cut_it():
    # live, 2026-11-06: the files said "A, B", the extension host says "A & B"; the scheme cuts groups to 32 characters
    assert ug.same_group("Alder Scans, Birch Translations", "Alder Scans & Birch Translations")
    assert ug.same_group("alder scans + birch translations", "Birch Translations & Alder Scans")
    assert ug.same_group("(SW) Scans, Galaxy Scans", "[SW] Scans & Galaxy Scans")
    assert ug.same_group("Alder Scans, Birch Transl\u2026", "Alder Scans & Birch Translations")
    assert ug.same_group("Alder Scans & Birch Translations", "Alder Scans, Bir\u2026")
    assert not ug.same_group("Alder Scans", "Alder Scans & Birch Translations")
    assert not ug.same_group("Alder Scans, Cedar\u2026", "Alder Scans & Birch Translations")
    assert not ug.same_group(OTHER, USUAL) and not ug.same_group("", "") and not ug.same_group(None, USUAL)
    held = {D(1): ["Alder Scans, Birch Translations"], D(2): ["Alder Scans, Birch Translations"]}
    choice = default_group({"Alder Scans & Birch Translations": 1, OTHER: 2}, held, [D(3)])
    assert choice == default_group.__globals__["GroupChoice"](
        "Alder Scans & Birch Translations", "the folder's usual group", "Alder Scans & Birch Translations")
    # the usual group's own copy, spelled the host's way, is not provisional
    assert ug.extra_for([chapter(301, "3", "Alder Scans & Birch Translations")],
                        "Alder Scans, Birch Translations") == ({}, [])


def test_only_a_chapter_from_another_group_is_provisional():
    picks = [chapter(204, "4", OTHER), chapter(205, "5", USUAL.upper()), chapter(206, "6", None)]
    now = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
    extra, waived = ug.extra_for(picks, USUAL, now=now)
    assert extra == {204: {ug.KEY_USUAL: USUAL, ug.KEY_UNTIL: "2026-11-20"},
                     206: {ug.KEY_USUAL: USUAL, ug.KEY_UNTIL: "2026-11-20"}} and waived == []
    assert ug.extra_for(picks, None) == ({}, [])                                  # no usual group: nothing
    extra, waived = ug.extra_for(picks[:1], USUAL, {"4": {ug.KEY_REPLACES: 7, ug.KEY_REPLACES_GROUP: OTHER}})
    assert extra == {204: {ug.KEY_REPLACES: 7, ug.KEY_REPLACES_GROUP: OTHER}} and waived == ["4"]


def test_what_the_owner_is_told():
    req = {"scanlator": OTHER, ug.KEY_USUAL: USUAL, ug.KEY_UNTIL: "2026-11-20"}
    assert ug.note_text(req, S.REMOVED) == (f"from {OTHER}; replaced by {USUAL}'s copy when it is released (watched "
                                            "until 2026-11-20)")
    assert ug.note_text({**req, ug.KEY_REPLACED_BY: 9}, S.REMOVED) == f"from {OTHER}; replaced by {USUAL}'s copy"
    assert ug.note_text(req, S.FAILED) == ""
    new = {"scanlator": USUAL, ug.KEY_REPLACES: 8, ug.KEY_REPLACES_GROUP: OTHER}
    assert ug.note_text(new, S.SENT) == f"replaces {OTHER}'s copy"
    assert ug.note_text(new, S.REMOVED) == f"replaced {OTHER}'s copy (it is in the holding folder)"
    assert ug.note_text(new, S.FAILED) == f"{OTHER}'s copy is kept"
    assert ug.note_text({"scanlator": USUAL}, S.REMOVED) == ""


# --- the whole story -------------------------------------------------------------------------------------------

def test_the_usual_groups_copy_replaces_the_other_groups_when_it_is_released(db, lib, tmp_path):
    check(db, lib)                                       # ch 4 is out from another group only: taken at once
    (rec,) = records(lib).values()
    req = lib.backend.chapter_ledger.request(rec.id)
    assert rec.group == OTHER and req[ug.KEY_USUAL] == USUAL
    assert rec.usual_note.startswith(f"from {OTHER}; replaced by {USUAL}'s copy when it is released")
    arrive(lib, 204)
    old_name = f"Ch. 0004.00 Vol. 001 (Example Title 204) [{OTHER}].cbz"
    assert lib.backend.chapter_ledger.get(rec.id).status == S.REMOVED and old_name in files(lib)
    assert "- done - from Cedar Scans; replaced by" in chapter_status_text(lib.backend.chapter_ledger.get(rec.id))

    report = check(db, lib)                              # nothing new: nothing sent
    assert report.usual == 0 and len(records(lib)) == 1

    lib.suwayomi.chapter_list.append(chapter(304, "4", USUAL))      # the usual group releases it the next day
    report = check(db, lib)
    assert report.usual == 1 and ", 1 usual group's copy sent to replace another's" in report.summary
    assert any(f"ch 4 from {USUAL} replaces {OTHER}'s copy: Sent ch 4" in line for line in report.lines)
    new = records(lib)["304"]
    assert new.status == S.SENT and new.usual_note == f"replaces {OTHER}'s copy"
    assert new.target_dir == str(lib.folder)
    assert len(records(lib)) == 2 and check(db, lib).usual == 0     # on its way: not sent twice

    arrive(lib, 304)
    new_name = f"Ch. 0004.00 Vol. 001 (Example Title 304) [{USUAL}].cbz"
    assert new_name in files(lib) and old_name not in files(lib)
    new, old = lib.backend.chapter_ledger.get(new.id), lib.backend.chapter_ledger.get(rec.id)
    assert new.status == S.REMOVED and new.usual_note == f"replaced {OTHER}'s copy (it is in the holding folder)"
    assert old.usual_note == f"from {OTHER}; replaced by {USUAL}'s copy"
    (batch,) = ReplacementStore(db).with_status("held")
    assert batch.files[0].rel == old_name and batch.files[0].chapters == "4" and batch.volumes == ()
    held = list((tmp_path / "appdata" / "replaced").rglob("*.cbz"))
    assert [p.name for p in held] == [old_name]
    assert check(db, lib).usual == 0                     # replaced: the watch is over

    upgrades.restore_batch(db, batch.id)                 # restorable, as any replaced file
    assert old_name in files(lib) and new_name in files(lib)


def test_the_owners_own_choice_and_a_hand_picked_chapter_are_not_provisional(db, lib):
    lib.backend.suwayomi.set_series_choice(lib.series_id, group=OTHER)       # the owner chose the other group
    check(db, lib)
    (rec,) = records(lib).values()
    assert ug.KEY_USUAL not in lib.backend.chapter_ledger.request(rec.id) and rec.usual_note == ""
    # the chapters panel (the owner picks the copy): no usual group is passed, nothing is provisional
    match = MangaMatch(MANGADEX, MANGA, HOW_MANGADEX)
    lib.suwayomi.chapter_list.append(chapter(205, "5", OTHER))
    out = send_chapters(lib.suwayomi, lib.backend.chapter_ledger, lib.series_id, match, [lib.suwayomi.by_id(205)],
                        str(lib.folder))
    assert ug.KEY_USUAL not in lib.backend.chapter_ledger.request(out.records[0].id)


def test_a_series_without_a_usual_group_takes_the_chapter_as_before(db, lib):
    for p in lib.folder.iterdir():
        p.rename(p.with_name(p.name.replace(f" [{USUAL}]", "")))           # no file names a group
    scan(db)
    check(db, lib)
    (rec,) = records(lib).values()
    assert rec.group == OTHER and ug.KEY_USUAL not in lib.backend.chapter_ledger.request(rec.id)


# --- the watch -------------------------------------------------------------------------------------------------

def test_the_watch_ends_after_watch_days_and_when_the_file_is_gone(db, lib):
    check(db, lib)
    arrive(lib, 204)
    ledger = lib.backend.chapter_ledger
    (w,) = ug.watched(db, ledger, lib.series_id)
    assert (w.number, w.group, w.usual, w.source_id) == ("4", OTHER, USUAL, MANGADEX.id)
    later = datetime.now(timezone.utc) + timedelta(days=ug.WATCH_DAYS + 1)
    assert ug.watched(db, ledger, lib.series_id, now=later) == []
    (lib.folder / f"Ch. 0004.00 Vol. 001 (Example Title 204) [{OTHER}].cbz").unlink()   # the owner deleted it
    assert ug.watched(db, ledger, lib.series_id) == []


def test_a_failed_replacement_is_tried_again_a_few_times(db, lib):
    check(db, lib)
    arrive(lib, 204)
    ledger = lib.backend.chapter_ledger
    lib.suwayomi.chapter_list.append(chapter(304, "4", USUAL))
    for n in range(ug.MAX_TRIES):
        assert check(db, lib).usual == 1
        new = max((r for r in ledger.all_records() if r.info_hash == "304"), key=lambda r: r.id)
        lib.suwayomi.queued.pop(304)                     # the program gave up on it
        ledger.set_status(new.id, S.FAILED, expect=(S.SENT,), error="the source dropped it")
    assert ug.watched(db, ledger, lib.series_id) == [] and check(db, lib).usual == 0
    assert ledger.get(new.id).usual_note == f"{OTHER}'s copy is kept"


def test_a_replacement_the_owner_cancelled_ends_the_watch(db, lib):
    check(db, lib)
    arrive(lib, 204)
    ledger = lib.backend.chapter_ledger
    lib.suwayomi.chapter_list.append(chapter(304, "4", USUAL))
    assert check(db, lib).usual == 1
    lib.suwayomi.queued.pop(304)
    ledger.cancel(records(lib)["304"].id)
    assert ug.watched(db, ledger, lib.series_id) == [] and check(db, lib).usual == 0


def test_another_form_is_not_a_replacement(db, lib):
    check(db, lib)
    arrive(lib, 204)
    # the usual group posts chapter 4 in two parts: the series holds the whole, so nothing is replaced
    lib.suwayomi.chapter_list += [chapter(314, "4.1", USUAL), chapter(324, "4.2", USUAL)]
    (w,) = ug.watched(db, lib.backend.chapter_ledger, lib.series_id)
    pairs, notes = ug.usual_copies(lib.suwayomi.chapter_list, [w], source_id=MANGADEX.id)
    assert pairs == [] and notes == [f"ch 4: {USUAL} released it as ch 4.1, 4.2 - another form, so {OTHER}'s copy "
                                     "is kept (one form per chapter)"]
    assert check(db, lib).usual == 0
    # another source's chapter 4 is not this one (sources number differently)
    lib.suwayomi.chapter_list.append(chapter(304, "4", USUAL))
    assert ug.usual_copies(lib.suwayomi.chapter_list, [w], source_id="12345")[0] == []
    pairs, _ = ug.usual_copies(lib.suwayomi.chapter_list, [w], source_id=MANGADEX.id, queued_ids=[304])
    assert pairs == []                                   # in the download queue already


def test_the_old_copy_waits_while_the_holding_folder_cannot_be_used(db, lib, tmp_path):
    check(db, lib)
    arrive(lib, 204)
    lib.suwayomi.chapter_list.append(chapter(304, "4", USUAL))
    check(db, lib)
    db.set_setting(upgrades.KEY_HOLDING_FOLDER, str(lib.folder / "inside"))  # inside a root: refused
    report = arrive(lib, 304)
    new = records(lib)["304"]
    assert new.status == S.DOWNLOADED and "waits for the holding folder" in dict(report.waiting)[new.id]
    assert f"Ch. 0004.00 Vol. 001 (Example Title 204) [{OTHER}].cbz" in files(lib)          # nothing moved
    upgrades.set_holding_folder(db, str(tmp_path / "appdata" / "replaced"))
    arrive(lib, 304)
    assert records(lib)["304"].status == S.REMOVED
    assert files(lib).count(f"Ch. 0004.00 Vol. 001 (Example Title 304) [{USUAL}].cbz") == 1
    assert f"Ch. 0004.00 Vol. 001 (Example Title 204) [{OTHER}].cbz" not in files(lib)


def test_a_waiting_finding_the_owner_sends_is_provisional_too(db, lib):
    from mangalist.downloads.finder import send_finding

    FindingStore(db).set_mode([lib.series_id], "notify")
    check(db, lib)                                       # Notify: the new chapter waits in Found
    (f,) = FindingStore(db).list("waiting")
    assert f.payload["usual"] == USUAL
    assert send_finding(db, f.id, backend=lib.backend, wanted=[]) == "Sent ch 4 to Suwayomi."
    (rec,) = records(lib).values()
    assert lib.backend.chapter_ledger.request(rec.id)[ug.KEY_USUAL] == USUAL
    arrive(lib, 204)
    # the owner's answer stands for the chapter: on Notify too, the usual group's copy is sent when it is released
    lib.suwayomi.chapter_list.append(chapter(304, "4", USUAL))
    assert check(db, lib).usual == 1


def test_the_usual_group_spelled_another_way_by_the_source_is_found(db, lib):
    for p in lib.folder.iterdir():                       # the files say "A, B"; the source says "A & B"
        p.rename(p.with_name(p.name.replace(USUAL, "Alder, Birch Scans")))
    scan(db)
    check(db, lib)
    (rec,) = records(lib).values()
    assert lib.backend.chapter_ledger.request(rec.id)[ug.KEY_USUAL] == "Alder, Birch Scans"
    arrive(lib, 204)
    lib.suwayomi.chapter_list.append(chapter(304, "4", "Alder & Birch Scans"))
    assert check(db, lib).usual == 1
    arrive(lib, 304)
    assert "Ch. 0004.00 Vol. 001 (Example Title 304) [Alder & Birch Scans].cbz" in files(lib)
    assert not any(OTHER in n for n in files(lib))
