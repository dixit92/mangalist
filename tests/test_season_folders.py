"""Season / part subfolders belong to their parent series (owner, 2026-10-10: a webtoon split into "Season 01" ...
"Season 04" showed as four series, each missing the chapters the others hold) - MangaPixer's unit-subfolder rule."""

from __future__ import annotations

from decimal import Decimal as D
from pathlib import Path

from mangalist.downloads import chapters as chm
from mangalist.scanner import scan_root
from mangalist.states import GAP_CHAPTER, State, compute_state, fallback_inventory_from_entry
from mangalist.store.units import Unit

from .states.helpers import own


def touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")


def _season(root: Path, series: str, season: str, numbers) -> None:
    for n in numbers:
        touch(root / series / season / f"Ch. {n:04d}.00.cbz")


def test_season_folders_are_one_series(tmp_path):
    root = tmp_path / "Manhwa"
    _season(root, "Example Hunter", "Season 01", range(1, 41))
    _season(root, "Example Hunter", "Season 02", range(41, 81))
    _season(root, "Example Hunter", "Season 03", range(81, 101))
    touch(root / "Part Story" / "Part 1" / "Part Story v01.cbz")          # a lone "Part 1" (the backlog's Part-N bug)
    touch(root / "Franchise" / "First Work" / "First Work v01.cbz")       # a franchise keeps its works apart
    touch(root / "Franchise" / "Second Work" / "Second Work v01.cbz")
    touch(root / "Franchise" / "Extras" / "omake.cbz")
    entries = {e.folder.relative_to(root).as_posix(): e for e in scan_root(root)}
    assert set(entries) == {"Example Hunter", "Part Story", "Franchise", "Franchise/First Work", "Franchise/Second Work"}
    hunter = entries["Example Hunter"]
    assert hunter.title == "Example Hunter" and len(hunter.files) == 100 and hunter.parent_folder is None
    assert not hunter.restarting_units                                     # 1-40, 41-80, 81-100: one numbering
    assert len(entries["Part Story"].files) == 1
    # the franchise parent holds its unit folder (Extras); its works are series of their own
    assert [f.path.name for f in entries["Franchise"].inventory_files] == ["omake.cbz"]


def test_numbering_that_restarts_in_each_season_is_not_compared(tmp_path):
    root = tmp_path / "Manhwa"
    _season(root, "Restarting", "Season 1", range(1, 31))
    _season(root, "Restarting", "Season 2", range(1, 21))
    (entry,) = scan_root(root)
    assert entry.restarting_units == frozenset({"chapter"})
    k = own(licensed_en=False, latest_chapter="80")
    inv = fallback_inventory_from_entry(entry)
    st = compute_state(inv, k, folder_empty=False, restarts=entry.restarting_units)
    assert st.state == State.CANT_TELL and not st.gaps and "start again" in st.reasons[0]
    # the same files without the restart rule would invent chapters 31-80
    plain = compute_state(inv, k, folder_empty=False)
    assert any(g.kind == GAP_CHAPTER for g in plain.gaps)


def test_a_duplicate_at_a_season_boundary_is_not_a_restart(tmp_path):
    root = tmp_path / "Manhwa"
    _season(root, "Boundary", "Season 1", range(1, 41))
    _season(root, "Boundary", "Season 2", range(40, 81))                 # chapter 40 in both
    (entry,) = scan_root(root)
    assert not entry.restarting_units


def test_new_chapters_go_to_the_latest_season(tmp_path):
    s = tmp_path / "Example Hunter"
    for season in ("Season 01", "Season 02", "Season 03"):
        (s / season).mkdir(parents=True)
    units = [Unit("Season 01/Ch. 0001.00.cbz", "chapter", ch_from="1", ch_to="1"),
             Unit("Season 01/Ch. 0040.00.cbz", "chapter", ch_from="40", ch_to="40"),
             Unit("Season 03/Ch. 0099.00.cbz", "chapter", ch_from="99", ch_to="99"),
             Unit("Season 02/Ch. 0080.00.cbz", "chapter", ch_from="80", ch_to="80")]
    p = chm.chapter_placement(str(s), units)
    assert p.target_dir == str(s / "Season 03") and not p.ambiguous and '"Season 03"' in p.reason
    assert p.options == (str(s / "Season 03"), str(s / "Season 02"), str(s / "Season 01"), str(s))
    # other folders than seasons / parts: ask, as before
    (s / "Misc").mkdir()
    mixed = chm.chapter_placement(str(s), units + [Unit("Misc/Ch. 0100.00.cbz", "chapter", ch_from="100", ch_to="100")])
    assert mixed.ambiguous


def test_old_season_rows_are_folded_into_the_parent_not_listed_missing(tmp_path, monkeypatch):
    # The library database of a scan made before the rule: one series row per season folder. The first scan with the
    # rule makes the parent the series; the season rows carry over to it instead of showing as missing series.
    from mangalist import scanner, store
    from mangalist.identity.carry import missing_count
    from mangalist.scanner import record_library_scan, scan_library

    store.reset_stores()
    db = store.get_store()
    root = tmp_path / "Manhwa"
    _season(root, "Example Hunter", "Season 01", range(1, 3))
    _season(root, "Example Hunter", "Season 02", range(3, 5))
    r = db.add_root(str(root), "Manhwa")
    monkeypatch.setattr(scanner, "_is_unit_subfolder", lambda name: False)        # the old behaviour
    record_library_scan(db, scan_library(db.list_roots(), db=db), pair=False)
    assert sorted(s.rel_path for s in db.list_series(r.id)) == ["Example Hunter/Season 01", "Example Hunter/Season 02"]
    s1 = db.get_series(r.id, "Example Hunter/Season 01").id
    with db.connect() as con:                                                     # a download that pointed at a season
        con.execute("INSERT INTO ledger (series_id, request, tool, status, created_at, updated_at) VALUES (?, '{}',"
                    " 'suwayomi', 'removed', 'now', 'now')", (s1,))
    monkeypatch.undo()
    record_library_scan(db, scan_library(db.list_roots(), db=db), pair=False)
    live = [s for s in db.list_series(r.id) if s.status == "present"]
    assert [s.rel_path for s in live] == ["Example Hunter"] and missing_count(db) == 0
    with db.connect() as con:
        assert con.execute("SELECT series_id FROM ledger").fetchone()[0] == live[0].id
        hows = {row[0] for row in con.execute("SELECT how FROM series_carries")}
    assert hows == {"unit-folder"}
    assert {a.series_id for a in db.list_archives(r.id)} == {live[0].id}
