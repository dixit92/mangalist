"""Port of the matcher-relevant part of MangaPixer 1.31.1 ``MangaUpdatesProviderTests.cs`` (status line, publisher
notes, webtoon vote, provider text flattening, the record -> candidate mapping) and of its golden harness's
recorded-mapping check. The publication status words are not ported (Manga-List reads them elsewhere), so
only the totals are asserted. Synthetic records, plus one recorded public fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manga_list.matcher import mangaupdates as mu

FIXTURES = Path(__file__).resolve().parents[1] / "golden" / "fixtures"


def cat(name, plus, minus):
    return {"category": name, "votes": plus - minus, "votes_plus": plus, "votes_minus": minus}


def test_webtoon_tri_state_uses_net_vote_threshold():
    assert mu.webtoon_of(None) is None
    assert mu.webtoon_of([]) is None
    assert mu.webtoon_of([cat("Magic", 10, 0)]) is False
    assert mu.webtoon_of([cat("Webtoon/Webcomic", 5, 1)]) is None  # 4 net: unsure
    assert mu.webtoon_of([cat("Webtoon/Webcomic", 6, 1)]) is True
    assert mu.webtoon_of([{"category": "webtoon/webcomic", "votes": 9}]) is True


@pytest.mark.parametrize(("status", "volumes"), [
    ("43 Volumes (Ongoing)", 43),
    ("200 Chapters + Prologue (Complete)  \n15 Volumes (Complete)", 15),
    ("12 Chapters (Ongoing)", None),
    ("3 Volumes (Hiatus)", 3),
    ("2 Volumes (Discontinued)", 2),
    ("1 Volume (Complete)", 1),
    ("see notes", None),
])
def test_status_parser_reads_volumes(status, volumes):
    assert mu.parse_status(status)[0] == volumes


@pytest.mark.parametrize(("status", "chapters"), [
    ("195 Chapters (Hiatus)  \nS1: 123 Chapters  \nS2: 72 Chapters", 195),
    ("200 Chapters + Prologue (Complete)  \n15 Volumes (Complete)", 200),
    ("18 Volumes (Ongoing)\n652 Chapters (Ongoing)", 652),
    ("1 Chapter (Complete)", 1),
    ("8 Volumes | 40 Chapters (Complete)", 40),  # 1.30.1: both totals on one line
    ("10 Volumes / 60 Chapters (Complete)", 60),
    ("43 Volumes (Ongoing)", None),
    ("24 Volumes (Complete)\n\nS1: 110 Chapters (1-110)", None),  # a season line is a part, never the total
    ("Part 1: 60 Chapters", None),
])
def test_status_parser_reads_the_chapter_total_from_a_line_that_starts_with_it_or_after_a_volume_total(status, chapters):
    assert mu.parse_status(status)[1] == chapters


def test_status_parser_ignores_blank_text():
    assert mu.parse_status("  ") == (None, None)


@pytest.mark.parametrize(("notes", "volumes", "chapters"), [
    ("10 Volumes / 60 Chapters; Ongoing", 10, 60),
    ("86 Chapters; Ongoing", None, 86),
    ("22 Volumes; Completed", 22, None),
    ("12 Volumes (Ongoing)", 12, None),
    ("13+2 Volumes; Complete", 15, None),
    ("18 Physical Volumes; Complete | 9 Physical Perfect Edition Omnibuses; Complete", 18, None),
    ("Digital, Print", None, None),
    ("Cancelled", None, None),
    (None, None, None),
    # The shapes stored in the recorded MangaUpdates fixtures (1.30.0): several editions per note, omnibus editions.
    ("14 Volumes; Ongoing", 14, None),
    ("5 Vols - Complete", 5, None),
    ("11 Vol - Ongoing", 11, None),
    ("201 Chapters; Defunct", None, 201),
    ("42 Volumes - Ongoing  | 14 Omnibus; print, 3-in-1 - Ongoing", 42, None),
    ("7 Physical Volumes; Ongoing", 7, None),
    ("13 Digital Volumes; Hiatus", 13, None),
    ("12 Volumes; Complete | 3 Volumes (Dropped)", 12, None),
    ("5 Omnibus Volumes; Complete", None, None),
    ("6 Volumes (3-in-1); Ongoing", None, None),
    ("Dropped", None, None),
    ("1995, 2008", None, None),
    ("", None, None),
])
def test_publisher_edition_reads_the_regular_editions_totals(notes, volumes, chapters):
    assert mu.parse_publisher_edition(notes) == (volumes, chapters)


def test_mapping_carries_the_chapter_total_and_the_webtoon_flag():
    c = mu.map_series({
        "series_id": 42, "title": "Synthetic Tower", "type": "Manhwa", "latest_chapter": 18,
        "status": "195 Chapters (Hiatus)\nS1: 123 Chapters\nS2: 72 Chapters",
        "categories": [{"category": "Webtoon/Webcomic", "votes": 20, "votes_plus": 20, "votes_minus": 0}],
    })
    # The live 1.26.1 miss: the candidate the scorer saw had neither (count veto at 195 vs 18).
    assert (c.total_chapters, c.latest_chapter, c.webtoon) == (195, 18, True)


def test_mapping_an_omnibus_only_count_never_feeds_the_english_total():
    c = mu.map_series({
        "series_id": 44, "title": "Synthetic Omnibus", "type": "Manga", "status": "20 Volumes (Complete)",
        "publishers": [
            {"publisher_name": "Omnibus House", "type": "English", "notes": "7 Omnibus Volumes; Complete"},
            {"publisher_name": "Gone Press", "type": "English", "notes": "4 Volumes; Defunct"},
            {"publisher_name": "Origin House", "type": "Original", "notes": "99 Volumes"},
        ],
    })
    assert (c.volumes, c.english_volumes, c.english_chapters) == (20, 4, None)


def test_mapping_flattens_text_and_deduplicates_alt_titles_against_the_title():
    c = mu.map_series({
        "series_id": 7, "title": "Alpha &amp; Beta", "type": "Manga",
        "associated": [{"title": "alpha & beta"}, {"title": "**Gamma**"}, {"title": "Gamma"}, {"title": "  "}],
        "authors": [{"name": "SMITH Anna", "type": "Author"}, {"name": "SMITH Anna", "type": "Artist"},
                    {"name": "JONES Bert", "type": "Artist"}],
        "related_series": [{"relation_type": "Spin-Off", "related_series_id": 8}, {"relation_type": "Sequel", "related_series_id": 0}],
    })
    assert c.title == "Alpha & Beta"
    assert c.alt_titles == ("Gamma",)
    assert c.authors == ("SMITH Anna", "JONES Bert")
    assert [(r.external_id, r.relation) for r in c.relations] == [("8", "spin-off")]


def test_search_page_keeps_the_total_and_drops_unusable_hits():
    page = mu.map_search_page({"total_hits": 37, "results": [
        {"record": {"series_id": 1, "title": "Alpha", "type": "Manga", "year": "2001"}, "hit_title": "ALPHA"},
        {"record": {"series_id": 2, "title": "Beta"}, "hit_title": "Beta Alias"},
        {"record": {"series_id": 0, "title": "No Id"}},
        {"record": {"series_id": 3, "title": "<p></p>"}},
    ]})
    assert page.total_hits == 37
    assert [(h.external_id, h.alt_titles, h.start_year) for h in page.hits] == [("1", (), 2001), ("2", ("Beta Alias",), None)]


def test_recorded_english_publisher_notes_reach_the_candidate():
    # MangaPixer's golden harness check on the recorded Solo Leveling record ("13+2 Volumes", "201 Chapters").
    record = json.loads((FIXTURES / "series.15180124327.json").read_text(encoding="utf-8"))
    c = mu.map_series(record)
    assert (c.english_volumes, c.english_chapters) == (15, 201)
    assert c.total_chapters == 200  # "200 Chapters + Prologue (Complete)"
    assert c.webtoon is True


def test_flatten_removes_markup_and_bounds_length():
    flat = mu.flatten("**Bold** and *it* [label](https://x.example/a_(b)) <b>tag</b> &amp; <br>next\u0007\n\n\n\nend", 1000)
    assert flat == "Bold and it label tag &\nnext\n\nend"
    assert len(mu.flatten("a" * 50, 5)) == 5
    assert mu.flatten("<p></p>", 10) is None
    assert mu.line("a\n\nb", 10) == "a b"


def test_flatten_strips_markdown_headings_quotes_and_emphasis_keeping_the_words():
    raw = ("A delinquent finds a baby on the riverbank.\n\n##### Notes:\nIncludes a __one-shot__ and *extra* pages.\n"
           "> Quoted from the _publisher_.\n#Hashtag stays\n### \nsnake_case_name stays")
    assert mu.flatten(raw, 1000) == ("A delinquent finds a baby on the riverbank.\n\nNotes:\nIncludes a one-shot and extra pages.\n"
                                     "Quoted from the publisher.\n#Hashtag stays\n\nsnake_case_name stays")


@pytest.mark.parametrize(("raw", "expected"), [
    ("# Title", "Title"),
    ("###### Six", "Six"),
    ("   ## Indented", "Indented"),
    ("Price #1 in sales", "Price #1 in sales"),
    (">> nested quote", "nested quote"),
    ("a > b", "a > b"),
    ("&gt; encoded quote", "encoded quote"),
])
def test_flatten_markdown_markers(raw, expected):
    assert mu.flatten(raw, 1000) == expected
