"""The Download tab's Qt-free rules for upgrades: one search for a series in two nyaa groups, the upgrade line above
the releases, the replaced-chapters line and a batch's state in words."""

from __future__ import annotations

from mangalist.gui import download_rules as rules
from mangalist.gui.shell import GROUP_CHAPTERS, GROUP_UPGRADES, GROUP_VOLUMES, WantedSeries
from mangalist.store.replacements import Batch, ReplacedFile


def ws(group, missing, findable=True, gaps="", reason=""):
    return WantedSeries(3, "/lib/Series U", "Series U", group, gaps, missing=missing, held=("1",),
                        titles=("Series U",), findable=findable, reason=reason)


def batch(status="pending", files=3, mode="delete", error=None, series="/lib/Series U", purge_after=None):
    fs = tuple(ReplacedFile(f"{series}/c{n}.cbz", f"c{n}.cbz", 100, "2026-10-01T00:00:00+00:00", str(n), "1")
               for n in range(files))
    return Batch(id=1, download_id=1, series_id=3, root_path="/lib", series_dir=series, volumes=("1",),
                 volume_files=(), files=fs, kept=(), mode=mode, status=status, plan_id=None, holding_dir=None,
                 purge_after=purge_after, error=error, created_at="", updated_at="")


def test_a_series_in_both_nyaa_groups_is_one_search_for_every_volume():
    vols = ws(GROUP_VOLUMES, ("10",), gaps="Vol. 10")
    ups = ws(GROUP_UPGRADES, ("2", "1"), gaps="Upgrade vol. 1-2")
    merged = rules.merge_entries([ups, vols])
    assert merged.group == GROUP_VOLUMES and merged.missing == ("1", "2", "10")
    assert merged.gaps == "Vol. 10  ·  Upgrade vol. 1-2"
    assert rules.upgrade_volumes_of([ups, vols]) == ("2", "1")
    # Only one searchable: that one; none: the first in group order (its reason is shown).
    assert rules.merge_entries([ws(GROUP_CHAPTERS, ("5",), False), ups]) == ups
    off = ws(GROUP_UPGRADES, ("1",), False)
    chapters = ws(GROUP_CHAPTERS, ("5",), False, reason="needs Suwayomi")
    assert rules.merge_entries([off, chapters]) == chapters
    assert rules.upgrade_volumes_of([off]) == ()


def test_the_upgrade_line_says_what_happens_to_the_chapters():
    hold = rules.upgrade_note(("1", "2", "3"), "holding", 30)
    assert hold.startswith("Upgrade v01-v03:") and "holding folder, restorable for 30 days" in hold
    delete = rules.upgrade_note(("4",), "delete", 30)
    assert "listed for you to confirm" in delete and "nothing is deleted before you do" in delete


def test_the_replaced_line_asks_for_pending_and_tells_where_held_files_are():
    assert rules.replaced_bar_text([]) == ("", "")
    text, tone = rules.replaced_bar_text([batch(files=24), batch(files=1, series="/lib/Other")])
    assert text == "Replace 25 chapter files of 2 series with the volumes filed?" and tone == "warn"
    text, _ = rules.replaced_bar_text([batch(files=2, mode="holding", error="the root is busy")])
    assert text.endswith("(1 could not be moved yet)")
    text, tone = rules.replaced_bar_text([batch("held", files=1)])        # the space first (owner, 2026-10-10)
    assert text == "100 B of replaced chapters in the holding folder (1 chapter file, 1 series)" and tone == "muted"
    text, _ = rules.replaced_bar_text([batch("held", files=3), batch("held", files=2, series="/lib/Other")])
    assert text == "500 B of replaced chapters in the holding folder (5 chapter files, 2 series)"


def test_a_batch_state_in_words():
    assert rules.batch_status_text(batch()) == "Waiting for your answer"
    assert rules.batch_status_text(batch(mode="holding", error="the root is busy")) == "Not moved yet: the root is busy"
    assert rules.batch_status_text(batch("held", purge_after="2030-11-08T12:00:00+00:00")).startswith(
        "In the holding folder until Nov 8")
    assert rules.batch_status_text(batch("declined")) == "Kept"
    assert rules.batch_status_text(batch("failed", error="gone")) == "Failed: gone"
