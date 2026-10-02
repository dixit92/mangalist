"""Manga-List <-> stage-2 matcher bridge (``manga_list.mu_match``): folder shapes from scanned
entries, tiers on recorded MangaUpdates responses, and the display helpers. No Qt, no network;
titles are public ones from the golden-set fixtures, paths are synthetic."""

from __future__ import annotations

from pathlib import Path

from manga_list import mu_match
from manga_list.matcher import MatchBand, WorkClass
from manga_list import mu_client
from manga_list.matcher.mangaupdates import AUTO_SEARCH_FILTER, map_search_page, map_series
from manga_list.models import FileHit, MangaEntry

from .fixture_mu import FakeMangaUpdates

ROOT = Path("/library/Manga")


def _entry(name: str, files, root: Path = ROOT, parent: str | None = None) -> MangaEntry:
    base = root / parent if parent else root
    folder = base / name
    hits = [FileHit(path=folder / f, size=1, depth=len(Path(f).parts) - 1) for f in files]
    return MangaEntry(folder=folder, title=name, english_title=None, files=hits,
                      parent_folder=base if parent else None)


def _match(entry: MangaEntry, fake: FakeMangaUpdates | None = None):
    fake = fake or FakeMangaUpdates()

    def search(text, page):
        return map_search_page(fake.search_series_page(text, page, 10, list(AUTO_SEARCH_FILTER)))

    def get(external_id):
        try:
            return map_series(fake.get_series(int(external_id)), external_id)
        except Exception as exc:  # noqa: BLE001
            if mu_client.is_not_found(exc):
                return None
            raise

    return mu_match.match_entry(entry, search, get), fake


# --- folder shape ---------------------------------------------------------------------------

def test_folder_shape_top_level_entry():
    entry = _entry("Chainsaw Man", ["Chainsaw Man v02.cbz", "Chainsaw Man v01.cbz",
                                    "Volumes/Chainsaw Man v03.cbz", "Chapters/c001.cbz", "Chapters/c002.cbz"])
    shape = mu_match.folder_shape(entry)
    assert shape.display_name == "Chainsaw Man"
    assert shape.depth == 1
    assert shape.archive_names == ("Chainsaw Man v01.cbz", "Chainsaw Man v02.cbz")  # direct only, sorted
    assert [(s.display_name, s.descendant_archive_count) for s in shape.subfolders] == [("Chapters", 2), ("Volumes", 1)]
    # Unit subfolders carry their archive names, so the count rule reads their numbers.
    assert [s.archive_names for s in shape.subfolders] == [("c001.cbz", "c002.cbz"), ("Chainsaw Man v03.cbz",)]
    assert shape.parent_display_name is None
    assert shape.category_hint == "manga"  # the Manga Root is named exactly like a category


def test_folder_shape_other_subfolders_carry_no_names_and_shelf_words_are_no_category():
    entry = _entry("Berserk", ["Berserk v01.cbz", "Berserk Gaiden/Berserk Gaiden 1.cbz"], root=Path("/library/Ongoing"))
    shape = mu_match.folder_shape(entry)
    assert [(s.display_name, s.archive_names) for s in shape.subfolders] == [("Berserk Gaiden", None)]
    assert shape.category_hint is None  # a shelf word, not a category


def test_folder_shape_subseries_and_no_category():
    entry = _entry("Part 3", ["v01.cbz"], root=Path("/library/My Books"), parent="Some Franchise")
    shape = mu_match.folder_shape(entry)
    assert shape.depth == 2
    assert shape.parent_display_name == "Some Franchise"
    assert shape.category_hint is None


# --- tiers on recorded responses ------------------------------------------------------------

def test_series_folder_is_auto():
    entry = _entry("Berserk", [f"Berserk v{i:02d} (Digital).cbz" for i in range(1, 42)])
    match, fake = _match(entry)
    assert match.band == mu_match.BAND_AUTO
    assert match.classification.cls == WorkClass.SERIES
    assert match.top.candidate.external_id == "51239621230"
    assert match.title_score == 1.0
    # The automatic search always carries the fixed type filter.
    assert all(filters == AUTO_SEARCH_FILTER for _, filters, _ in fake.searches)


def test_category_root_is_positive_only_evidence():
    # The Manga Root says "Manga", the record is a manhwa: no penalty, no veto (MangaPixer 1.27.0).
    entry = _entry("Solo Leveling", [f"{i:03d} [Chapter Title {i}].cbz" for i in range(1, 201)])
    match, _ = _match(entry)
    assert match.band == mu_match.BAND_AUTO
    assert match.top.candidate.external_id == "15180124327"
    assert "TypeConflict" not in match.reasons


def test_count_conflict_is_review_with_reasons():
    entry = _entry("Look Back", [f"Look Back v{i:02d}.cbz" for i in range(1, 11)])
    match, _ = _match(entry)
    assert match.band == mu_match.BAND_REVIEW
    assert "CountConflict" in match.reasons


def test_spin_off_named_by_the_folder_subtitle_goes_to_review():
    entry = _entry("Attack on Titan - Before the Fall",
                   [f"Attack on Titan - Before the Fall - Chapter {i:03d}.cbz" for i in range(1, 59)])
    match, _ = _match(entry)
    assert match.band == mu_match.BAND_REVIEW
    assert match.top.candidate.external_id == "28267595998"
    assert "SubtitleFamily" in match.reasons
    assert "main series vs spin-off" in mu_match.reason_text(match.reasons)


def test_page_two_is_read_on_a_tie():
    entry = _entry("Jigokuraku", ["Jigokuraku v01.cbz", "Jigokuraku v02.cbz"])
    match, fake = _match(entry)
    assert match.band == mu_match.BAND_REVIEW
    assert [page for _, _, page in fake.searches] == [1, 2]


def test_a_failed_get_ends_the_lookup(monkeypatch):
    fake = FakeMangaUpdates()

    def broken(series_id):
        raise mu_client.requests.ConnectionError("offline")

    monkeypatch.setattr(fake, "get_series", broken)
    entry = _entry("Berserk", [f"Berserk v{i:02d}.cbz" for i in range(1, 6)])
    try:
        _match(entry, fake)
    except mu_client.requests.ConnectionError:
        pass
    else:
        raise AssertionError("a network failure must not be scored as a missing record")


def test_nothing_on_the_provider_is_unmatched():
    entry = _entry("Zzqx Nonexistent Synthetic Title", [f"Zzqx Nonexistent Synthetic Title v{i:02d}.cbz" for i in (1, 2, 3)])
    match, _ = _match(entry)
    assert match.band == mu_match.BAND_UNMATCHED
    assert match.top is None


def test_collection_folder_is_not_matched_and_sends_nothing():
    entry = _entry("Shelf", ["Look Back.cbz", "Sayonara Eri.cbz", "Hunter x Hunter v01.cbz",
                             "Akira v01.cbz", "Oyasumi Punpun v01.cbz"])
    match, fake = _match(entry)
    assert match.band == mu_match.BAND_NOT_A_WORK
    assert match.classification.cls == WorkClass.COLLECTION_LEAF
    assert fake.searches == [] and fake.gets == []


def test_unit_subfolder_row_is_not_matched():
    entry = _entry("Volumes", [f"Chainsaw Man v{i:02d}.cbz" for i in range(1, 12)], parent="Chainsaw Man")
    match, fake = _match(entry)
    assert match.band == mu_match.BAND_NOT_A_WORK
    assert match.classification.cls == WorkClass.UNIT_SUB
    assert fake.searches == []


def test_candidate_titles_deduplicated():
    entry = _entry("Berserk", [f"Berserk v{i:02d}.cbz" for i in range(1, 6)])
    match, _ = _match(entry)
    titles = mu_match.candidate_titles(match.top.candidate)
    assert titles[0] == "Berserk"
    assert len(titles) == len(set(titles))


# --- display helpers ------------------------------------------------------------------------

def _matched(**kw) -> MangaEntry:
    e = _entry("X", [])
    e.mu_id, e.mu_title, e.mu_score = 1, "Some Title", 0.8
    for k, v in kw.items():
        setattr(e, k, v)
    return e


def test_needs_review_only_for_unconfirmed_current_review_matches():
    assert mu_match.needs_review(_matched(mu_band="review"))
    assert not mu_match.needs_review(_matched(mu_band="auto"))
    assert not mu_match.needs_review(_matched(mu_band="review", mu_confirmed=True))
    # A legacy (version 1) score is never compared with the new tiers.
    assert not mu_match.needs_review(_matched(mu_band=None, mu_score=0.1, mu_score_version=1))


def test_legacy_score_tooltip_does_not_quote_the_old_score():
    e = _matched(mu_score=0.1, mu_score_version=1)
    assert mu_match.is_legacy_score(e)
    tip = mu_match.match_tooltip(e)
    assert "older Manga-List version" in tip and "10%" not in tip


def test_review_tooltip_lists_reasons():
    tip = mu_match.match_tooltip(_matched(mu_band="review", mu_reasons=["CountConflict", "CloseSecond"]))
    assert "needs review" in tip and "80%" in tip
    assert "volume / chapter numbers go far past the record" in tip and "close second candidate" in tip


def test_tooltips_for_rows_without_a_match():
    e = _entry("Shelf", [])
    e.mu_band, e.mu_work_class = mu_match.BAND_NOT_A_WORK, "COLLECTION_LEAF"
    assert "collection of separate works" in mu_match.match_tooltip(e)
    e.mu_band = mu_match.BAND_UNMATCHED
    assert "No confident" in mu_match.match_tooltip(e)
    e.mu_band = None
    assert mu_match.match_tooltip(e) is None


def test_outcome_bands_map_to_row_bands():
    assert set(mu_match._BAND_BY_OUTCOME) == set(MatchBand)
