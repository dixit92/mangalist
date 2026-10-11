"""The wording of the automatic-downloads screens (no widgets): the modes, what a finding would get and its size, the
Send all question, and when the last find pass ran. Made-up titles."""

from __future__ import annotations

from datetime import datetime, timezone

from mangalist.downloads.finding import FindReport
from mangalist.gui import automation_rules as ar

from .finding_fakes import FakeFindingBackend, find_run, finding
from .conftest import FakeBackend

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


def test_a_backend_has_automation_only_with_every_method():
    assert ar.has_automation(FakeFindingBackend())
    assert not ar.has_automation(FakeBackend()) and not ar.has_automation(None)


def test_mode_texts_say_when_a_series_follows_the_default():
    assert ar.mode_text("auto", "notify") == "Auto"
    assert ar.mode_text(None, "notify") == "Notify (the default)"
    assert ar.default_entry_label("off") == "Use the default (Off)"
    assert set(ar.MODE_HELP) == set(ar.MODES) and all(t.endswith(".") for t in ar.MODE_HELP.values())


def test_the_menu_ticks_only_what_every_selected_series_has():
    assert ar.common_choice(["auto", "auto"]) == "auto"
    assert ar.common_choice([None, None]) == ar.DEFAULT_KEY
    assert ar.common_choice(["auto", None]) is None
    assert ar.set_mode_text("auto", 3, "notify") == "Automatic downloads: Auto for 3 series."
    assert ar.set_mode_text(None, 1, "notify") == "Automatic downloads: 1 series follow the default again (Notify)."


def test_what_a_finding_gets_uses_exact_numbers():
    assert ar.gets_text(finding(fills=("3", "4", "5"))) == "v03-v05"
    assert ar.gets_text(finding(kind="upgrades", fills=("13",))) == "v13"
    assert ar.gets_text(finding(kind="chapters", fills=("101", "102", "102.5"))) == "ch 101-102, 102.5"
    assert ar.size_text(None) == "size not known" and ar.size_text(734_003_200) == "700 MB"


def test_totals_and_the_send_all_question_name_the_count_and_the_size():
    found = [finding(1, size_bytes=1_073_741_824), finding(2, size_bytes=None),
             finding(3, kind="chapters", fills=("41",), size_bytes=None)]
    assert ar.total_text(found) == "3 waiting · 1 GB + 2 of size not known"
    title, text = ar.send_all_question(found)
    assert title == "Send all 3?"
    assert "2 torrents (1 GB plus 1 of a size not known yet)" in text and "1 set of chapters (Suwayomi)" in text
    assert "download budget" in text
    assert "budget" not in ar.send_all_question([found[2]])[1]                 # chapters only: no budget
    assert ar.send_all_summary([("A v03", None), ("B v01", "refused")]) == (
        "Sent 1 of 2. 1 could not be sent - B v01: refused", "bad")


def test_the_last_pass_line():
    assert ar.last_run_text(None) == ar.NEVER_RAN
    line = ar.last_run_text(find_run(trigger="schedule"), NOW)
    assert line.startswith("Last find pass: ") and "(scheduled) - 12 series searched, 1 sent, 2 waiting for you." in line
    assert ar.last_run_text(find_run(finished=False), NOW).startswith("A find pass is running (scheduled, started ")
    report = FindReport(run_id=1, trigger="now", series_searched=4, found=1, sent=0, errors=1)
    assert ar.report_text(report) == "Find now: 4 series searched, 0 sent, 1 waiting for you, 1 failed."
    assert ar.report_text(report, bulk=True).startswith("Bulk find: ")
    assert ar.bulk_find_label(3) == "Find releases for 3 series"
