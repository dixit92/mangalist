"""The Download tab's Qt-free rules: the groups and their filter, a row's status text, the badge colours, the wording of
times and schedules, the release's "why" line, and the switches stored in the settings (no Qt)."""

from __future__ import annotations

from datetime import datetime, timezone

from mangalist.downloads.contracts import DownloadStatus as S
from mangalist.downloads.options import (
    KEY_MU_AUTOSTART,
    KEY_SCAN_AFTER_FILING,
    NyaaOptions,
    get_flag,
    load_nyaa_options,
    save_nyaa_options,
    set_flag,
)
from mangalist.gui import download_rules as rules
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries

from .fakes import candidate


def ws(title, group=GROUP_VOLUMES, folder=None, **kw):
    return WantedSeries(1, folder or f"/lib/{title}", title, group, kw.pop("gaps", "Vol. 2"), **kw)


def record(status, **kw):
    from mangalist.downloads.contracts import DownloadRecord

    base = dict(id=1, series_id=1, info_hash="a" * 40, title="Series A v02", wanted_volumes=("2",), target_dir="/lib/A",
                status=status, created_at="2026-10-07T10:00:00+00:00", updated_at="2026-10-07T10:05:00+00:00")
    base.update(kw)
    return DownloadRecord(**base)


def test_groups_come_in_the_fixed_order_sorted_by_title_and_filtered():
    items = [ws("Zeta"), ws("alpha"), ws("Ch One", GROUP_CHAPTERS), ws("Up", GROUP_UPGRADES),
             ws("Beta", titles=("Beta", "Bêta Alt"))]
    out = rules.grouped(items)
    assert [g for g, _ in out] == [GROUP_VOLUMES, GROUP_CHAPTERS, GROUP_UPGRADES]
    assert [s.title for s in out[0][1]] == ["alpha", "Beta", "Zeta"]
    assert [s.title for s in rules.grouped(items, "ALPH")[0][1]] == ["alpha"]
    assert [s.title for s in rules.grouped(items, "alt")[0][1]] == ["Beta"]            # an alternative title matches too
    assert [len(items) for _g, items in rules.grouped([], "x")] == [0, 0, 0]            # empty groups stay
    assert rules.count_text(31, 31) == "31 series" and rules.count_text(2, 31) == "2 of 31 series"


def test_group_notes_and_reasons():
    assert rules.GROUP_NOTES[GROUP_VOLUMES][0] == "nyaa" and rules.GROUP_NOTES[GROUP_CHAPTERS][0] == "needs Suwayomi"
    assert rules.GROUP_NOTES[GROUP_UPGRADES][0] == "coming later"
    assert rules.not_findable_reason(ws("A", findable=True)) == ""
    assert rules.not_findable_reason(ws("A", reason="Not licensed in English")) == "Not licensed in English"
    assert "Suwayomi" in rules.not_findable_reason(ws("C", GROUP_CHAPTERS, findable=True))
    assert "later version" in rules.not_findable_reason(ws("U", GROUP_UPGRADES))


def test_row_status_precedence():
    sent, filed = record(S.SENT), record(S.FILED, wanted_volumes=("3", "4"))
    assert rules.row_status(None, None) == ""
    assert rules.row_status(None, rules.SEARCH_READY) == "Releases ready"
    assert rules.row_status(None, rules.SEARCH_NONE) == "No releases" and rules.row_status(None, rules.SEARCH_FAILED) == "Search failed"
    assert rules.row_status(sent, rules.SEARCH_READY) == "Sent"                  # a live download outranks a search result
    assert rules.row_status(sent, rules.SEARCH_RUNNING) == "Searching..."        # a search in flight outranks a download
    assert rules.row_status(filed, rules.SEARCH_READY) == "Releases ready"       # filed is history: a new search is news
    assert rules.row_status(filed, None) == "Filed v03-v04 - seeding"
    assert rules.row_status(record(S.FAILED, error="no space"), None) == "Failed: no space"
    assert rules.row_status(None, rules.SEARCH_QUEUED) == "Queued"


def test_badge_kinds():
    kinds = {s: rules.badge_kind(record(s)) for s in S.ALL}
    assert kinds == {S.SENT: "run", S.DOWNLOADED: "run", S.FILED: "ok", S.REMOVED: "done", S.FAILED: "bad",
                     S.CANCELLED: "done"}


def test_times_and_schedules_are_worded_like_the_mockup():
    now = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
    local = lambda iso: datetime.fromisoformat(iso).astimezone()                      # noqa: E731
    today = "2026-10-08T09:48:00+00:00"
    assert rules.when_text(today, now) == local(today).strftime("%H:%M") or rules.when_text(today, now).startswith("Oct")
    assert rules.when_text("2026-10-07T19:21:00+00:00", now).split()[0] == "Oct"
    assert rules.when_text("", now) == "" and rules.when_text("not a time", now) == ""
    assert rules.next_check_text(None) == "Checks run automatically every hour"
    assert rules.next_check_text(today, now).startswith("Next automatic check ")
    assert rules.schedule_text("daily@03:30") == "daily 03:30"
    assert rules.schedule_text("every 1h") == "every hour" and rules.schedule_text("every 12h") == "every 12 hours"


def test_schedule_rows_read_the_container_environment():
    rows = {w: (when, env) for w, when, env in rules.schedule_rows({})}
    assert rows == {"Rescan the library": ("daily 03:30", False), "Sync with MangaPixer": ("daily 03:15", False),
                    "File finished downloads": ("every hour (and Check now)", False)}
    rows = {w: (when, env) for w, when, env in rules.schedule_rows(
        {"MANGALIST_RESCAN_SCHEDULE": "off", "MANGALIST_MANGAPIXER_SYNC_SCHEDULE": "daily@02:00",
         "MANGALIST_DOWNLOADS_SCHEDULE": "banana"})}
    assert rows["Rescan the library"] == ("off", True) and rows["Sync with MangaPixer"] == ("daily 02:00", True)
    assert rows["File finished downloads"][0] == "banana (not understood)"


def test_release_why_line():
    plain = candidate()
    plain = type(plain)(**{**plain.__dict__, "reasons": ("Digital", "covers the missing volume")})
    assert rules.release_why(plain) == "Digital, covers the missing volume"
    odd = type(plain)(**{**plain.__dict__, "not_comic": True, "remake": True, "trusted": True, "reasons": ()})
    assert rules.release_why(odd) == "light novel / not a comic release, marked as a remake on nyaa, trusted uploader"
    assert rules.release_why(type(plain)(**{**plain.__dict__, "reasons": (), "trusted": False})) == "no ranking reasons given"


def test_names_for_records():
    recs = [record(S.SENT, series_id=1), record(S.SENT, id=2, series_id=2), record(S.SENT, id=3, series_id=3)]
    names = rules.series_names_for(recs, [ws("Wanted One")], {2: "Backend Two"})
    assert names == {1: "Wanted One", 2: "Backend Two", 3: "Series #3"}


class _Store:
    def __init__(self):
        self.data = {}

    def get_setting(self, key, default=None):
        return self.data.get(key, default)

    def set_setting(self, key, value):
        self.data[key] = value


def test_switches_default_and_round_trip():
    store = _Store()
    assert load_nyaa_options(store) == NyaaOptions()
    assert get_flag(store, KEY_MU_AUTOSTART) is False and get_flag(store, KEY_SCAN_AFTER_FILING) is True
    set_flag(store, KEY_MU_AUTOSTART, True)
    set_flag(store, KEY_SCAN_AFTER_FILING, False)
    assert get_flag(store, KEY_MU_AUTOSTART) is True and get_flag(store, KEY_SCAN_AFTER_FILING) is False
    save_nyaa_options(store, NyaaOptions(enabled=False, raw=True, digital_first=False))
    got = load_nyaa_options(store)
    assert got.enabled is False and got.raw is True and got.digital_first is True      # the fixed ones stay on
    store.data["downloads.nyaa"] = {"english": "yes", "raw": True, "junk": 1}
    assert load_nyaa_options(store) == NyaaOptions(raw=True)                           # a wrong type falls back
    store.data["mu_autostart"] = "maybe"
    assert get_flag(store, KEY_MU_AUTOSTART) is False                                 # a wrong type: the default
