"""Comics extras read as MangaPixer 1.42.0 reads them (owner, 2026-10-15, question 13: "No, parity with MangaPixer"):
``Annual 2`` is chapter 2 flagged extra - and, as in MangaPixer's Missing (``MissingUnits.FileNumbersOf`` skips an
extra), it never fills a missing chapter 2, never counts as the highest chapter, is not a copy of chapter 2, and the
renamer leaves it as it is. Made-up titles only."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from mangalist.duplicates import stated_unit
from mangalist.inventory import as_state_inventory, compute_inventory, units_from_parsed
from mangalist.knowledge import SeriesKnowledge
from mangalist.models import FileHit, MangaEntry
from mangalist.parsing import Kind, parse_name
from mangalist.renamer import LEFT_ALONE, DefaultNamer, plan_files
from mangalist.states import GAP_CHAPTER, State, compute_state

NAMES = ["Ch. 0001.00 [Grp].cbz", "Ch. 0003.00 [Grp].cbz", "Some Comic Annual 2.cbz", "Some Comic Special #12.cbz"]


def _units(names):
    out = []
    for n in names:
        out += units_from_parsed(n, parse_name(n))
    return out


def test_a_numbered_comics_extra_is_an_extra_row_that_keeps_its_number():
    (row,) = units_from_parsed("Some Comic Annual 2.cbz", parse_name("Some Comic Annual 2.cbz"))
    assert (row.kind, row.ch_from, row.ch_to) == ("extra", "2", "2")
    (plain,) = units_from_parsed("Some Comic Annual.cbz", parse_name("Some Comic Annual.cbz"))
    assert (plain.kind, plain.ch_from) == ("extra", None)
    assert stated_unit([row]) is None                                  # never a copy of chapter 2


def test_it_never_fills_a_missing_chapter_or_raises_the_highest_chapter():
    inv = compute_inventory(_units(NAMES))
    assert [str(c) for c in inv.chapters] == ["1", "3"]
    assert set(inv.extras) == {"Some Comic Annual 2.cbz", "Some Comic Special #12.cbz"}
    assert inv.highest_chapter == Decimal(3)
    k = SeriesKnowledge(source="own-matcher", link_state="Auto", mu_id="1", title="Some Comic", latest_chapter="3",
                        licensed_en=False)
    st = compute_state(as_state_inventory(inv), k, folder_empty=False)
    assert st.state == State.MISSING_CHAPTERS
    assert [(g.start, g.end) for g in st.gaps if g.kind == GAP_CHAPTER] == [("2", None)]     # Annual 2 is not it


def test_the_folder_view_skips_it_too(tmp_path):
    entry = MangaEntry(folder=tmp_path / "Some Comic", title="Some Comic", english_title=None)
    entry.files = [FileHit(path=entry.folder / n, size=10, depth=0, parsed=parse_name(n)) for n in NAMES]
    assert entry.max_disk_chapter == 3.0                               # not 12 (Special #12)
    assert entry.restarting_units == frozenset()


def test_the_renamer_leaves_it_alone_and_says_why(tmp_path):
    files = []
    for n in ["Some Comic Annual 2.cbz", "Some Comic c002 (2020) (Digital) (Grp).cbz"]:
        p = tmp_path / n
        p.write_bytes(b"x" * 10)
        files.append((str(p), parse_name(n)))
    namer = DefaultNamer()
    plans = {Path(p.path).name: p for p in plan_files(files, namer=namer, limits=namer.limits(None),
                                                       series_title="Some Comic")}
    annual = plans["Some Comic Annual 2.cbz"]
    assert annual.status == LEFT_ALONE and annual.target is None
    assert "comics extra" in annual.reason and "chapter 2" in annual.reason
    assert plans["Some Comic c002 (2020) (Digital) (Grp).cbz"].target.startswith("Ch. 0002.00")   # the real chapter 2


def test_a_held_copy_of_chapter_2_is_not_the_annual(tmp_path):
    from mangalist.downloads.chapter_arrivals import _ChapterPass

    folder = tmp_path / "Some Comic"
    folder.mkdir()
    (folder / "Some Comic Annual 2.cbz").write_bytes(b"x")
    assert _ChapterPass._held_copy(str(folder), "2") is None
    (folder / "Ch. 0002.00 [Grp].cbz").write_bytes(b"x")
    assert _ChapterPass._held_copy(str(folder), "2") == "Ch. 0002.00 [Grp].cbz"


def test_a_year_is_not_a_number_and_a_token_name_is_not_an_extra():
    fcbd = parse_name("Some Comic FCBD 2019.cbz")
    assert (fcbd.is_extra, fcbd.chapter) == (True, None)
    issue = parse_name("Some Comic Annual c003.cbz")
    assert issue.kind is Kind.CHAPTER and not issue.is_extra and str(issue.chapter) == "3"
