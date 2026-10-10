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
    assert rules.GROUP_NOTES[GROUP_UPGRADES][0] == "nyaa"                 # upgrades search nyaa (volumes cycle)
    assert rules.not_findable_reason(ws("A", findable=True)) == ""
    assert rules.not_findable_reason(ws("A", reason="Not licensed in English")) == "Not licensed in English"
    assert "Suwayomi" in rules.not_findable_reason(ws("C", GROUP_CHAPTERS, findable=True))
    assert "cannot be upgraded from nyaa yet" in rules.not_findable_reason(ws("U", GROUP_UPGRADES))
    assert "cannot be upgraded" in rules.not_findable_reason(ws("U", GROUP_UPGRADES, reason="coming later"))
    assert rules.not_findable_reason(ws("U", GROUP_UPGRADES, findable=True)) == ""
    assert rules.not_findable_reason(ws("U", GROUP_UPGRADES, reason="Not licensed in English")) == \
        "Not licensed in English"


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
                    "File finished downloads": ("every hour (and Check qBittorrent now)", False)}
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


# --- the "To get" row chips (owner, 2026-10-09: "user should be aware if there's a torrent already under download") ---

def test_row_chips_show_every_torrent_in_qbittorrent_and_then_the_search():
    sent = record(S.SENT, id=5, wanted_volumes=("36",))
    filed = record(S.FILED, id=2, wanted_volumes=("9", "10"))
    assert rules.row_chips([], None) == []
    assert rules.row_chips([], rules.SEARCH_READY) == [("Releases ready", "ready")]
    assert rules.row_chips([filed], rules.SEARCH_READY) == [("Seeding v09-v10", "ok"), ("Releases ready", "ready")]
    assert rules.row_chips([filed, sent], None) == [("Downloading v36", "run"), ("Seeding v09-v10", "ok")]  # newest first
    assert rules.row_chips([record(S.DOWNLOADED)], rules.SEARCH_RUNNING) == [("Downloaded v02", "run"),
                                                                            ("Searching...", "muted")]
    assert rules.row_chips([record(S.FAILED, error="no space")], None) == [("Failed: no space", "bad")]
    stopped = record(S.FILED, error="stopped in qBittorrent")
    assert rules.row_chips([stopped], None) == [("Filed v02 - stopped in qBittorrent", "ok")]


def test_row_chips_cap_the_downloads_and_show_a_finished_one_only_alone():
    many = [record(S.SENT, id=i, wanted_volumes=(str(i),)) for i in range(1, 5)]
    chips = rules.row_chips(many, rules.SEARCH_NONE)
    assert chips == [("Downloading v04", "run"), ("Downloading v03", "run"), ("+2", "muted"), ("No releases", "muted")]
    removed = record(S.REMOVED)
    assert rules.row_chips([removed], None) == [("Filed v02 - done", "done")]
    assert rules.row_chips([removed], rules.SEARCH_READY) == [("Releases ready", "ready")]          # history yields
    assert rules.row_chips([record(S.CANCELLED)], None) == []
    assert rules.chips_text(chips) == "Downloading v04 · Downloading v03 · +2 · No releases"


def test_in_qbittorrent_keeps_the_torrents_still_there_newest_first():
    rows = [record(S.FILED, id=1), record(S.REMOVED, id=2), record(S.SENT, id=3), record(S.FAILED, id=4),
            record(S.DOWNLOADED, id=5), record(S.CANCELLED, id=6)]
    assert [r.id for r in rules.in_qbittorrent(rows)] == [5, 3, 1]
