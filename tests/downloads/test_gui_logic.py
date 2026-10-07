"""The volumes GUI's Qt-free rules: when "Find volumes on nyaa..." is enabled (and the reason when not), the
search titles, the exact-number wording, the download status texts, and the backend factory."""

from __future__ import annotations

from mangalist.downloads.contracts import DownloadRecord, DownloadStatus
from mangalist.gui import downloads_backend
from mangalist.gui.downloads_backend import QbtSettings, create_backend
from mangalist.gui.volumes_target import (
    find_volumes_availability, latest_by_series, numbers_text, search_titles, status_text, status_tooltip, volume_label,
)
from mangalist.knowledge import (
    LINK_AUTO, LINK_CONFIRMED, LINK_DONT_MATCH, LINK_NEEDS_REVIEW, SOURCE_MANGAPIXER, SOURCE_OWN_MATCHER,
    SeriesKnowledge,
)
from mangalist.states import GAP_CHAPTER, GAP_VOLUME, Gap, SeriesState, State


def _knowledge(**kw) -> SeriesKnowledge:
    base = dict(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="123", title="Example Series",
                alt_titles=("Exemplar", "example series"))
    base.update(kw)
    return SeriesKnowledge(**base)


def _state(*missing, chapters=()) -> SeriesState:
    gaps = tuple(Gap(GAP_VOLUME, str(v)) for v in missing) + tuple(Gap(GAP_CHAPTER, str(c)) for c in chapters)
    return SeriesState(State.MISSING_VOLUMES if missing else State.UP_TO_DATE, gaps=gaps)


def _availability(knowledge, state, series_id=7, held=("1",)):
    return find_volumes_availability(series_id=series_id, folder="/lib/Example Series", title="Example Series",
                                     english_title=None, knowledge=knowledge, state=state, held=held)


def test_enabled_for_a_mangapixer_series_with_missing_volumes():
    got = _availability(_knowledge(link_state=LINK_AUTO), _state(3, 4))
    assert got.enabled and got.reason == ""
    assert got.target.series_id == 7 and got.target.missing == ("3", "4") and got.target.held == ("1",)
    assert got.target.titles == ("Example Series", "Exemplar")          # case-insensitive duplicate dropped


def test_disabled_without_mangapixer_with_the_reason():
    own = _availability(_knowledge(source=SOURCE_OWN_MATCHER), _state(3))
    assert not own.enabled and "MangaPixer" in own.reason and own.target is None
    assert not _availability(None, _state(3)).enabled


def test_disabled_when_mangapixer_has_no_match_for_it():
    review = _availability(_knowledge(link_state=LINK_NEEDS_REVIEW), _state(3))
    assert not review.enabled and "MangaPixer" in review.reason
    dont = _availability(_knowledge(link_state=LINK_DONT_MATCH, mu_id=None, title=None), _state(3))
    assert not dont.enabled and "Don't match" in dont.reason


def test_disabled_without_missing_volumes_or_a_scanned_folder():
    none_missing = _availability(_knowledge(), _state())
    assert not none_missing.enabled and "No missing volumes" in none_missing.reason
    only_chapters = _availability(_knowledge(), _state(chapters=(10, 11)))
    assert not only_chapters.enabled and "No missing volumes" in only_chapters.reason
    assert not _availability(_knowledge(), None).enabled
    unscanned = _availability(_knowledge(), _state(3), series_id=None)
    assert not unscanned.enabled and "rescan" in unscanned.reason


def test_search_titles_main_first_deduplicated_and_capped():
    k = _knowledge(english_title="Example Series EN", alt_titles=tuple(f"Alt {i}" for i in range(20)))
    titles = search_titles(k, "Folder Name", "example series en")
    assert titles[0] == "Example Series EN" and titles[1] == "Example Series" and len(titles) == 10
    assert search_titles(_knowledge(alt_titles=()), None, "  ") == ("Example Series",)


def test_numbers_are_exact_and_merge_whole_runs():
    assert numbers_text(["5", "3", "4", "9"]) == "3-5, 9"
    assert numbers_text(["3", "4", "5", "9"], pad=True) == "v03-v05, v09"
    assert numbers_text(["1", "2", "2.5", "3"]) == "1-2, 2.5, 3"
    assert numbers_text(["12.50", "10", "10"], pad=True) == "v10, v12.5"
    assert numbers_text([]) == "" and volume_label("12") == "v12" and volume_label("100") == "v100"


def _record(status, **kw) -> DownloadRecord:
    base = dict(id=1, series_id=7, info_hash="a" * 40, title="Example Series v03-05", wanted_volumes=("3", "4", "5"),
                target_dir="/lib/Example Series", status=status, created_at="2026-10-07T10:00:00Z",
                updated_at="2026-10-07T10:05:00Z")
    base.update(kw)
    return DownloadRecord(**base)


def test_status_texts():
    assert status_text(_record(DownloadStatus.SENT)) == "Sent"
    assert status_text(_record(DownloadStatus.DOWNLOADED)) == "Downloaded"
    assert status_text(_record(DownloadStatus.FILED)) == "Filed v03-v05"
    assert status_text(_record(DownloadStatus.FILED, wanted_volumes=("7",))) == "Filed v07"
    assert status_text(_record(DownloadStatus.FAILED, error="no space left")) == "Failed: no space left"
    assert status_text(_record(DownloadStatus.FAILED)) == "Failed"
    assert status_text(_record(DownloadStatus.REMOVED)) == "Removed"
    assert status_text(_record(DownloadStatus.CANCELLED)) == "Cancelled"


def test_status_tooltip_warns_about_a_copy():
    tip = status_tooltip(_record(DownloadStatus.FILED, copied=True))
    assert "/lib/Example Series" in tip and "double the space" in tip


def test_latest_record_per_series_wins():
    got = latest_by_series([_record(DownloadStatus.FILED, id=1), _record(DownloadStatus.SENT, id=3),
                            _record(DownloadStatus.FAILED, id=2), _record(DownloadStatus.SENT, id=4, series_id=9)])
    assert got[7].id == 3 and got[9].id == 4


def test_backend_factory_needs_the_switch_and_an_adapter(monkeypatch):
    monkeypatch.delenv("MANGALIST_DOWNLOADS", raising=False)
    assert create_backend(object()) is None                                    # switched off
    monkeypatch.setenv("MANGALIST_DOWNLOADS", "1")
    assert create_backend(object()) is None                                    # on, but no adapter on this branch

    class Adapter:
        @staticmethod
        def create_backend(db):
            return ("backend", db)

    monkeypatch.setattr(downloads_backend.importlib, "import_module", lambda name: Adapter)
    assert create_backend("db") == ("backend", "db")


def test_settings_default_and_no_password_in_repr():
    s = QbtSettings()
    assert s.save_path == "/data/appdata/torrents/mangalist" and s.remove_completed and s.verify_tls
    assert not s.has_password and "password" not in {f for f in vars(s) if f != "has_password"}
