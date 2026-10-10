"""Parser fixes of the renamer cycle (owner's library, 2026-10-10: most "extra" and "unknown" files were chapters or
volumes the parser did not read): labelled chapters, FMD2 brackets that start with the chapter number, "Part N"
inside a volume release's title, bare numbers after the series title. Synthetic names only."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from mangalist.models import FileHit
from mangalist.parsing import Kind, Layer, ParseContext, parse_bare, parse_fmd2, parse_labelled, parse_name

D = Decimal


def _u(r):
    return None if r is None else str(r)


# --- FMD2: labelled chapters in the bracket head ----------------------------------------------------------------

@pytest.mark.parametrize("name, vol, ch, title, group", [
    ("0012 [Contact. 0001 - Hello [G]].cbz", None, "1", "Hello", "G"),
    ("0012 [Contact 0012].cbz", None, "12", None, None),
    ("0035 [episode 0035].cbz", None, "35", None, None),
    ("0035 [Episode 35 - The Return [Team [X]]].cbz", None, "35", "The Return", "Team [X]"),
    ("0036 [Ep. 36: Title].cbz", None, "36", "Title", None),
    ("0015 [Pact 0015].cbz", None, "15", None, None),
    ("0011 [report011. A Title].cbz", None, "11", "A Title", None),
    ("0003 [Act 3_ The End].cbz", None, "3", "The End", None),
    ("0004 [Lesson 4.5 - Extra Lesson].cbz", None, "4.5", "Extra Lesson", None),
    ("0005 [Stage 5].cbz", None, "5", None, None),
    ("0006 [Round 6 [G]].cbz", None, "6", None, "G"),
    ("0007 [Night 7-8].cbz", None, "7-8", None, None),
    ("0051 [Vol. 2 Episode 5 - T [G]].cbz", "2", "5", "T", "G"),
    ("Some Series - 0045 [Episode 45].cbz", None, "45", None, None),
])
def test_fmd2_labelled_heads(name, vol, ch, title, group):
    r = parse_fmd2(name)
    assert r is not None and r.layer is Layer.FMD2 and r.kind is Kind.CHAPTER and not r.is_extra
    assert (_u(r.volume), _u(r.chapter), r.title, r.group) == (vol, ch, title, group)


# --- FMD2: a bracket that starts with the chapter number (arc parts) --------------------------------------------

@pytest.mark.parametrize("name, ch, title, group", [
    ("0076 [0076  The Arc Title (3)].cbz", "76", "The Arc Title (3)", None),
    ("0077 [0077  The Arc Title (4) [G]].cbz", "77", "The Arc Title (4)", "G"),
    ("Some Series - 0011 [0011 report011. A Title].cbz", "11", "A Title", None),   # the label says it again
    ("0012 [0012 A Title].cbz", "12", "A Title", None),                             # zero-padded
    ("0050 [100].cbz", "100", None, None),
    ("0041 [12 - Title [G]].cbz", "12", "Title", "G"),
    ("0042 [12. Title].cbz", "12", "Title", None),
    ("0043 [12: Title].cbz", "12", "Title", None),
    ("0044 [12 [G]].cbz", "12", None, "G"),
    ("0045 [12.5  Title].cbz", "12.5", "Title", None),
])
def test_fmd2_number_heads(name, ch, title, group):
    r = parse_fmd2(name)
    assert r.kind is Kind.CHAPTER and (_u(r.chapter), r.title, r.group) == (ch, title, group)
    assert r.volume is None and not r.is_extra


@pytest.mark.parametrize("name, title", [
    ("0001 [3 Days Later].cbz", "3 Days Later"),
    ("0002 [1 Year Later [G]].cbz", "1 Year Later"),
    ("0003 [100 Days].cbz", "100 Days"),
    ("0004 [2nd Season 05].cbz", "2nd Season 05"),
])
def test_a_number_that_starts_a_title_is_not_the_chapter(name, title):
    r = parse_fmd2(name)
    assert r.chapter is None and r.title == title


@pytest.mark.parametrize("name, title", [
    ("0101 [Omake [G]].cbz", "Omake"),
    ("0102 [Side Story 2].cbz", "Side Story 2"),             # "Story" is not a chapter label: still an extra
    ("0103 [Vol. 2 - Contact Lens [G]].cbz", "Contact Lens"),
])
def test_extras_and_volume_titles_stay(name, title):
    r = parse_fmd2(name)
    assert r.chapter is None and r.title == title


def test_fmd2_title_after_the_head_is_free_text():
    r = parse_name("0010 [Ch. 0010 - The Vol 2 Begins [Grp]].cbz")
    assert (str(r.chapter), r.volume, r.title) == ("10", None, "The Vol 2 Begins")
    r = parse_name("0010 [Contact 10 - Back to Vol. 2, Ch. 3 [Grp]].cbz")
    assert (str(r.chapter), r.volume, r.title) == ("10", None, "Back to Vol. 2, Ch. 3")


# --- labelled chapters outside FMD2 brackets ----------------------------------------------------------------------

@pytest.mark.parametrize("name, series, vol, ch, title, group", [
    ("Some Series - Episode 35.cbz", "Some Series", None, "35", None, None),
    ("Some Series - Episode 35 (2023) (Digital) (Grp).cbz", "Some Series", None, "35", None, "Grp"),
    ("Some Series Contact. 0001 - Hello [G].cbz", "Some Series", None, "1", "Hello", "G"),
    ("Some Series [Pact 0015].cbz", "Some Series", None, "15", None, None),
    ("Some Series [Pact 0015 - A Title] (Grp).cbz", "Some Series", None, "15", "A Title", "Grp"),
    ("Some Series (Episode 3).cbz", "Some Series", None, "3", None, None),
    ("Some Series - Report 3 - The Fall [Grp].cbz", "Some Series", None, "3", "The Fall", "Grp"),
    ("Some Series Vol. 2 Lesson 5.cbz", "Some Series", "2", "5", None, None),
    ("Some Series v03 Stage 12.5  A Title.cbz", "Some Series", "3", "12.5", "A Title", None),
    ("Some Series Episode 4 (v01) [G].cbz", "Some Series", "1", "4", None, "G"),
    ("Some Series_Ep_07.cbz", "Some Series", None, "7", None, None),
    ("Some Series Night 7-8.cbz", "Some Series", None, "7-8", None, None),
    ("Lesson 5.cbz", None, None, "5", None, None),
    ("Some Act 2 Series - Scene 3.cbz", "Some Act 2 Series", None, "3", None, None),
])
def test_labelled(name, series, vol, ch, title, group):
    r = parse_name(name)
    assert (r.layer, r.kind) == (Layer.LABELLED, Kind.CHAPTER), r.notes
    assert (r.series, _u(r.volume), _u(r.chapter), r.title, r.group) == (series, vol, ch, title, group)


@pytest.mark.parametrize("name", [
    "Level 99 Hero 03.cbz",                    # the label's number is followed by more of the series title
    "Some Series Ch. 5 Episode 2.cbz",         # a classifier chapter token: the generic layer reads it
    "Some Series Episode.cbz",                 # no number
    "Some Series Episode 5 Is Here 3.cbz",
    "Some Series [Pact 0015 extra words].cbz",
    "Stepladder 5.cbz",                        # "step" inside a word
])
def test_not_labelled(name):
    assert parse_labelled(name) is None


def test_the_series_title_is_skipped():
    # "Level 9 Case" is the folder's title: its words are not labels.
    r = parse_labelled("Level 9 Case - Episode 4.cbz", series_title="Level 9 Case")
    assert (str(r.chapter), r.series) == ("4", "Level 9 Case")
    r = parse_name("Level 9 Case - Episode 4.cbz", ParseContext(series_title="Level 9 Case"))
    assert str(r.chapter) == "4"


def test_labelled_names_do_not_change_the_generic_layer():
    for name in ("Some Series Vol. 1 Ch 3.cbz", "Some Series c012 + 013 (Grp).cbz", "Title chap 4.cbz"):
        assert parse_name(name).layer is Layer.GENERIC


# --- "Part N" inside a volume release's title -----------------------------------------------------------------

@pytest.mark.parametrize("name, series, vol, sub", [
    ("Some Series - Part 5 - Golden Sub v05 (2022) (Digital) (Grp).cbz", "Some Series - Part 5 - Golden Sub", "5",
     None),
    ("Some Series Part 5 v05 (2022) (Digital) (Grp).cbz", "Some Series Part 5", "5", None),
    ("Some Series - Part 3 - Sub v01-03 (2020) (Digital) (Grp).cbz", "Some Series - Part 3 - Sub", "1-3", None),
    ("Some Series Season 2 v07 - The Subtitle (2021) (Digital) (Grp).cbz", "Some Series Season 2", "7",
     "The Subtitle"),
])
def test_part_n_in_the_series_title(name, series, vol, sub):
    r = parse_name(name)
    assert (r.layer, r.kind) == (Layer.RELEASE, Kind.VOLUME)
    assert (r.series, str(r.volume), r.title, r.group, r.year) == (series, vol, sub, "Grp", r.year)


def test_a_release_bare_number_without_a_volume_token_is_unchanged():
    r = parse_name("Some Series 001 (2019) (Digital) (Grp).cbz")
    assert (r.layer, str(r.number), r.series) == (Layer.RELEASE, "1", "Some Series")


# --- bare numbers after the series title ---------------------------------------------------------------------

@pytest.mark.parametrize("name, ch, series, title, group, layer", [
    ("Some Series 07.cbz", "7", "Some Series", None, None, Layer.BARE),
    ("Some Series - 07.cbz", "7", "Some Series", None, None, Layer.BARE),
    ("Some Series. 035 (2023) (Digital) (Grp).cbz", "35", "Some Series", None, "Grp", Layer.RELEASE),
    ("Some Series 014.6  A Chapter Title.cbz", "14.6", "Some Series", "A Chapter Title", None, Layer.BARE),
    ("Some Series 014.6  A Chapter Title (Grp).cbz", "14.6", "Some Series", "A Chapter Title", "Grp", Layer.BARE),
    ("Some Series 07 - A Chapter Title.cbz", "7", "Some Series", "A Chapter Title", None, Layer.BARE),
    ("Some Series 12  A Chapter Title.cbz", "12", "Some Series", "A Chapter Title", None, Layer.BARE),
    ("Some Series 2.5. A Chapter Title.cbz", "2.5", "Some Series", "A Chapter Title", None, Layer.BARE),
])
def test_bare_number_after_the_title_is_a_guessed_chapter(name, ch, series, title, group, layer):
    r = parse_name(name)
    assert (r.layer, r.kind, r.guessed) == (layer, Kind.CHAPTER, True)
    assert (str(r.chapter), str(r.number), r.series, r.title, r.group) == (ch, ch, series, title, group)
    assert r.volume is None and r.legacy_kind == "chapter"


@pytest.mark.parametrize("name", ["Some Series 2 - The Return.cbz", "Some Series 3 Something.cbz"])
def test_a_number_inside_a_title_is_not_read(name):
    assert parse_name(name).number is None


@pytest.mark.parametrize("name", ["01.cbz", "001.5.cbz", "Vol. 01"])
def test_a_name_that_is_only_a_number_stays_unknown(name):
    r = parse_name(name)
    assert r.kind is Kind.UNKNOWN and r.number is not None and not r.guessed


def test_the_series_answer_decides_a_guess():
    for hint, kind in (("volumes", Kind.VOLUME), ("chapters", Kind.CHAPTER)):
        r = parse_name("Some Series 07.cbz", ParseContext(kind_hint=hint))
        assert (r.kind, r.guessed, str(r.number)) == (kind, False, "7")
        assert (str(r.volume) if kind is Kind.VOLUME else str(r.chapter)) == "7"
        assert (r.chapter if kind is Kind.VOLUME else r.volume) is None
    r = parse_name("Some Series 014.6  A Title.cbz", ParseContext(kind_hint="volumes"))
    assert (r.kind, str(r.volume), r.chapter, r.title) == (Kind.VOLUME, "14.6", None, "A Title")


def test_a_guess_keeps_the_question_open():
    guessed = FileHit(path=Path("Some Series 07.cbz"), size=0, depth=0, parsed=parse_name("Some Series 07.cbz"))
    assert guessed.kind == "chapter" and guessed.needs_kind
    answered = FileHit(path=Path("Some Series 07.cbz"), size=0, depth=0,
                       parsed=parse_name("Some Series 07.cbz", ParseContext(kind_hint="chapters")))
    assert answered.kind == "chapter" and not answered.needs_kind
    stated = FileHit(path=Path("0007 [Ch. 7].cbz"), size=0, depth=0, parsed=parse_name("0007 [Ch. 7].cbz"))
    assert not stated.needs_kind


def test_the_bare_layer_itself_still_reports_an_unknown_kind():
    r = parse_bare("Some Series 014.6  A Title.cbz")
    assert (r.kind, str(r.number), r.title, r.guessed) == (Kind.UNKNOWN, "14.6", "A Title", False)


# --- the generic layer flags several numbers ------------------------------------------------------------------

@pytest.mark.parametrize("name, ambiguous", [
    ("Title Vol 3 Vol 4.cbz", True), ("Title Ch. 1 Ch. 2 Ch. 3.cbz", True), ("Title c001 + c002.cbz", True),
    ("Vol. 1 Ch 3.cbz", False), ("Ch.10-15.cbz", False), ("Title Vol 3 Vol 3.cbz", False),
])
def test_generic_ambiguity(name, ambiguous):
    r = parse_name(name)
    assert r.layer is Layer.GENERIC and r.ambiguous is ambiguous
