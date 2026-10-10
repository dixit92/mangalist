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
    assert kinds == {S.QUEUED: "muted", S.SENT: "run", S.DOWNLOADED: "run", S.FILED: "ok", S.REMOVED: "done", S.FAILED: "bad",
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
                    "File finished downloads": ("every hour (and Check downloads now)", False)}
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


# --- the download budget: queued downloads ---------------------------------------------------------------------------

def test_a_queued_download_is_a_muted_chip_and_counts_as_in_hand():
    from mangalist.gui.volumes_target import status_text, status_tooltip

    queued = record(S.QUEUED, id=7, wanted_volumes=("36",), queue_position=2, size_bytes=3 * 1024 ** 3,
                    size_source="release")
    sent = record(S.SENT, id=3)
    assert rules.download_chip(queued) == ("Queued v36", "muted")
    assert rules.row_chips([sent, queued], rules.SEARCH_READY) == [("Queued v36", "muted"), ("Downloading v02", "run"),
                                                                   ("Releases ready", "ready")]
    assert [r.id for r in rules.in_hand([sent, queued, record(S.FAILED, id=9)])] == [7, 3]
    assert [r.id for r in rules.in_qbittorrent([sent, queued])] == [3]
    assert status_text(queued) == "Queued - 2nd in line"
    noted = record(S.QUEUED, error="bigger than the 20 GB cap on its own (30 GB): send it now, past the cap, or remove it")
    assert status_text(noted).startswith("Queued - bigger than the 20 GB cap on its own")
    tip = status_tooltip(queued)
    assert "Queued, 2nd in line" in tip and "Size: 3 GB (the release's size on nyaa" in tip
    assert "Counts against the download budget: 3 GB (as qBittorrent reports it)" in status_tooltip(
        record(S.FILED, size_bytes=3 * 1024 ** 3, size_source="qbittorrent"))


# --- schedule choices (owner, 2026-10-10: "good options are: Weekly, Daily, Every 12 hours and Every 6 hours") --------

def test_schedule_choices_and_their_text():
    labels = [l for _c, l in rules.schedule_choices("rescan", "daily@03:30")]
    assert labels == ["Weekly", "Daily", "Every 12 hours", "Every 6 hours", "Off"]
    assert [l for _c, l in rules.schedule_choices("downloads", "every 1h")][0] == "Every hour"
    assert ("every 3h", "Every 3 hours") in rules.schedule_choices("rescan", "every 3h")     # kept, never lost
    assert rules.ScheduleChoice("weekly", 2, 4, 5).text() == "weekly@wed 04:05"
    assert rules.ScheduleChoice("daily", hour=23, minute=0).text() == "daily@23:00"
    assert rules.ScheduleChoice("every 6h").text() == "every 6h"
    assert rules.choice_of("weekly@sat 01:02") == rules.ScheduleChoice("weekly", 5, 1, 2)
    assert rules.choice_of("daily@02:15") == rules.ScheduleChoice("daily", hour=2, minute=15)
    assert rules.choice_of("off").choice == "off" and rules.choice_of("whenever").choice == "whenever"
    assert rules.schedule_text("weekly@sun 03:30") == "every Sunday 03:30"


# --- chapter downloads (the Suwayomi MVP) ---------------------------------------------------------------------------

def chapter_rec(id, number, status=S.SENT, batch="b1", group="Alpha Scans", error=None):
    from mangalist.downloads.contracts import TOOL_SUWAYOMI

    return record(status, id=id, info_hash=str(id), title=f"Vol.1 Ch.{number} - Example", wanted_volumes=(),
                  tool=TOOL_SUWAYOMI, wanted_chapters=(number,), batch=batch, source="MangaDex (EN)", group=group,
                  error=error, updated_at=f"2026-10-10T10:0{id % 10}:00+00:00")


def test_the_chapters_group_note_and_why_a_series_cannot_be_looked_up():
    assert rules.group_note(GROUP_CHAPTERS) == rules.GROUP_NOTES[GROUP_CHAPTERS]
    assert rules.group_note(GROUP_CHAPTERS)[0] == "needs Suwayomi"
    assert rules.group_note(GROUP_CHAPTERS, True) == ("Suwayomi", "muted")
    assert rules.group_note(GROUP_VOLUMES, True) == rules.GROUP_NOTES[GROUP_VOLUMES]
    series = ws("C", GROUP_CHAPTERS, missing=("41",))
    assert "not set up" in rules.chapters_reason(series, False)
    assert rules.chapters_reason(series, True) == ""
    assert "rescan first" in rules.chapters_reason(WantedSeries(None, "/lib/C", "C", GROUP_CHAPTERS, "Ch. 41"), True)
    assert "not known" in rules.chapters_reason(ws("C", GROUP_CHAPTERS, missing=()), True)


def test_one_send_of_chapters_is_one_row_and_one_chip():
    torrent = record(S.SENT, id=9)
    recs = [chapter_rec(1, "101"), chapter_rec(2, "103"), chapter_rec(3, "102", group="Beta Group"), torrent,
            chapter_rec(4, "104", status=S.FAILED, error="Suwayomi did not take the download: x"),
            chapter_rec(5, "200", batch="b2")]
    merged = rules.merge_batches(recs)
    assert [r.id for r in merged] == [3, 9, 4, 5]                      # the first member's place, the newest id
    first = merged[0]
    assert first.wanted_chapters == ("101", "102", "103") and first.title == "Ch. 101-103 · Alpha Scans + Beta Group · " \
                                                                              "MangaDex (EN)"
    assert merged[1] is torrent and merged[2].wanted_chapters == ("104",)
    assert merged[3].title == "Vol.1 Ch.200 - Example"                  # a Send of one keeps its own title
    chips = rules.row_chips([r for r in recs if r.is_chapters], rules.SEARCH_READY, rules.CHAPTER_CHIPS)
    assert chips[:2] == [("Downloading ch 200", "run"), ("Failed: Suwayomi did not take the download: x", "bad")]
    assert chips[2] == ("+1", "muted") and chips[-1] == ("Chapters ready", "ready")
    # Not torrents: never "in qBittorrent", never in hand for a release.
    assert rules.in_qbittorrent(recs) == [torrent] and rules.in_hand(recs) == [torrent]


def test_chapter_chips_and_status_texts():
    from mangalist.gui.volumes_target import status_text, units_text

    assert units_text(chapter_rec(1, "10.5")) == "ch 10.5"
    assert rules.download_chip(chapter_rec(1, "7", S.FILED)) == ("Filed ch 7", "ok")           # no seeding
    assert rules.download_chip(chapter_rec(1, "7", S.REMOVED))[0] == "Filed ch 7"
    assert status_text(chapter_rec(1, "7", S.SENT)) == "Downloading"
    assert status_text(chapter_rec(1, "7", S.SENT, error="Suwayomi could not download it")) == \
        "Downloading - Suwayomi could not download it"
    assert status_text(chapter_rec(1, "7", S.FILED, error="Suwayomi's copy kept: x")) == "Filed ch 7 - Suwayomi's copy kept: x"
    assert status_text(chapter_rec(1, "7", S.REMOVED)) == "Filed ch 7 - done"
    assert status_text(chapter_rec(1, "7", S.DOWNLOADED)) == "Downloaded"
