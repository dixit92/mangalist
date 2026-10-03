"""SeriesKnowledge adapters: MangaPixer export items and the own matcher's MangaEntry fields."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

from mangalist import knowledge as kn
from mangalist.models import MangaEntry

from .helpers import TODAY, item


def test_exact_numbers():
    assert kn.to_decimal("24.5") == Decimal("24.5")
    assert kn.to_decimal(12.5) == Decimal("12.5")
    assert kn.to_decimal(0.1) == Decimal("0.1")          # through repr, not the binary value
    assert kn.to_decimal("291.999") == Decimal("291.999")
    assert kn.to_decimal(True) is None and kn.to_decimal("x") is None and kn.to_decimal("NaN") is None
    assert kn.fmt_num(Decimal("12.0")) == "12" and kn.fmt_num("24.50") == "24.5" and kn.fmt_num(100) == "100"
    assert kn.fmt_num("291.999") == "291.999" and kn.fmt_num(None) == ""


def test_partial_dates():
    assert kn.parse_partial_date("2025-07-01") == (2025, 7, 1)
    assert kn.parse_partial_date("2025-07") == (2025, 7)
    assert kn.parse_partial_date("2025") == (2025,)
    assert kn.parse_partial_date("2025-13") is None and kn.parse_partial_date("soon") is None
    assert kn.parse_partial_date("2025-02-30") is None
    today = dt.date(2026, 10, 3)
    assert kn.date_is_released("2026-10-03", today) is True
    assert kn.date_is_released("2026-10-04", today) is False
    assert kn.date_is_released("2026-10", today) is True       # same month: not after today
    assert kn.date_is_released("2026-11", today) is False
    assert kn.date_is_released("2026", today) is True
    assert kn.date_is_released("2027", today) is False
    assert kn.date_is_released(None, today) is None


def test_a_volume_date_beats_the_stale_export_kind():
    v = kn.VolumeInfo("4", english_date="2026-09-01", english_date_kind="announced")
    assert v.released(TODAY) is True               # announced at the rebuild, out by now
    assert kn.VolumeInfo("5", english_date_kind="announced").released(TODAY) is False
    assert kn.VolumeInfo("5", english_date_kind="released").released(TODAY) is True
    assert kn.VolumeInfo("5").released(TODAY) is None


def test_from_mangapixer_item():
    k = kn.from_mangapixer_item(item())
    assert k.source == kn.SOURCE_MANGAPIXER and k.link_state == "Confirmed" and k.matched
    assert (k.mu_id, k.title) == ("90000000001", "Example Quest")
    assert k.mu_url.startswith("https://www.mangaupdates.com/series/")
    assert k.latest_chapter == "41" and k.origin_volumes == Decimal(5) and k.licensed_en is True
    assert k.english_publishers == (kn.EnglishPublisher("Example Press", Decimal(3), None, "Ongoing"),)
    assert [v.volume for v in k.volumes] == ["1", "2", "3", "4"]
    assert k.volumes[2].chapters_to == "24.5" and k.volumes[3].chapters_from is None
    assert k.volumes[1].english_date == "2025-07"
    assert k.completion == kn.Completion("MissingSome", "Running", False, (), "2026-10-03T12:00:00.000Z")
    assert [link.label for link in k.official_links][0] == "Official (original language)"
    assert k.anilist_id == 900001 and k.anilist_volumes == Decimal(5)
    assert k.fetched_at == "2026-09-30T12:00:00.000Z" and k.next_due_at == "2026-10-14T12:00:00.000Z"
    assert k.search_title == "Example Quest"


def test_needs_review_and_dont_match_items_have_no_record():
    k = kn.from_mangapixer_item(item(link={"state": "NeedsReview"}, record=None, completion=None, volumes=None,
                                     officialLinks=[], refresh=None))
    assert k.needs_review and not k.matched and k.title is None and k.volumes == ()
    k = kn.from_mangapixer_item(item(link={"state": "DontMatch"}, record=None))
    assert k.link_state == kn.LINK_DONT_MATCH and not k.matched


def test_an_unknown_provider_is_ignored_like_no_record():
    k = kn.from_mangapixer_item(item(record__provider="gcd"))
    assert k.title is None and not k.matched


def test_completion_upgrade_volumes_are_exact_strings():
    k = kn.from_mangapixer_item(item(completion={"answer": "UpToDate", "reason": "None", "upgradeAvailable": True,
                                                 "upgradeVolumes": ["1", "2.5"]}))
    assert k.completion.upgrade_available and k.completion.upgrade_volumes == ("1", "2.5")


def _entry(**fields) -> MangaEntry:
    e = MangaEntry(folder=Path("/library/Example Saga"), title="Example Saga", english_title=None)
    for key, value in fields.items():
        setattr(e, key, value)
    return e


def test_from_own_matcher_entry_fields():
    e = _entry(mu_id=77, mu_title="Example Saga", mu_url="https://www.mangaupdates.com/series/x/example-saga",
               licensed=True, publisher_name="Example Press", publisher_volumes=7.0, publisher_chapters=None,
               publisher_status="Ongoing", scan_latest_chapter=60.5, scan_latest_volume=None,
               completed_in_origin=False, anilist_id=5, anilist_chapters=60.0, anilist_volumes=8.0, mu_band="auto")
    k = kn.from_own_matcher(e)
    assert k.source == kn.SOURCE_OWN_MATCHER and k.link_state == kn.LINK_AUTO and k.matched
    assert k.mu_id == "77" and k.latest_chapter == "60.5"
    assert k.english_publishers[0].name == "Example Press" and k.publisher_volumes == Decimal(7)
    assert k.anilist_volumes == Decimal(8) and k.completed_in_origin is False


def test_own_matcher_link_states():
    assert kn.from_own_matcher(_entry()).link_state == kn.LINK_NOT_LOOKED_UP
    assert kn.from_own_matcher(_entry(mu_band="unmatched")).link_state == kn.LINK_UNMATCHED
    assert kn.from_own_matcher(_entry(mu_band="not_a_work")).link_state == kn.LINK_NOT_A_WORK
    assert kn.from_own_matcher(_entry(mu_id=1, mu_title="T", mu_band="review")).link_state == kn.LINK_NEEDS_REVIEW
    assert kn.from_own_matcher(_entry(mu_id=1, mu_title="T", mu_band="review",
                                      mu_confirmed=True)).link_state == kn.LINK_CONFIRMED


def test_from_own_matcher_with_the_full_record_and_anilist():
    record = {
        "series_id": 77, "title": "Example Saga", "url": "https://www.mangaupdates.com/series/x/example-saga",
        "type": "Manga", "status": "12 Volumes (Complete)", "completed": True, "licensed": True,
        "latest_chapter": 98,
        "publishers": [
            {"publisher_name": "Origin House", "publisher_id": 1, "type": "Original", "notes": ""},
            {"publisher_name": "Example Press", "publisher_id": 2, "type": "English", "notes": "12 Volumes; Completed"},
            {"publisher_name": "Example Digital", "publisher_id": 3, "type": "English", "notes": "98 Chapters"},
        ],
    }
    anilist = {"id": 5, "title": "Example Saga EN", "english_title": "Example Saga EN", "chapters": 98, "volumes": 12,
               "external_links": [{"url": "https://reader.example.com/saga", "site": "Example Reader",
                                   "type": "STREAMING", "language": "English"}]}
    e = _entry(mu_id=77, mu_title="Example Saga", licensed=True, mu_confirmed=True)
    k = kn.from_own_matcher(e, record, anilist)
    assert [p.name for p in k.english_publishers] == ["Example Press", "Example Digital"]
    assert k.publisher_volumes == Decimal(12) and k.publisher_chapters == Decimal(98)
    assert k.english_finished and k.finished_in_origin and k.latest_chapter == "98"
    assert k.english_title == "Example Saga EN" and k.search_title == "Example Saga EN"
    assert k.anilist_links[0]["site"] == "Example Reader"
