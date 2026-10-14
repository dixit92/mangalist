"""The automatic-downloads screens against the real adapter (:class:`mangalist.downloads.adapter.Backend`) over a
temporary library database: the findings a pass offered show under Found with the series' name, Skip goes through the
store (never offered again), and the per-series mode and the default round-trip. Nothing is sent (no qBittorrent, no
network); made-up names."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from mangalist import store  # noqa: E402
from mangalist.downloads.adapter import Backend  # noqa: E402
from mangalist.gui.auto_modes import AutoModes  # noqa: E402
from mangalist.gui.automation_rules import has_automation  # noqa: E402
from mangalist.gui.download_tab import GROUP_FOUND, DownloadTab  # noqa: E402
from mangalist.store.findings import FindingStore, NewFinding  # noqa: E402

from ..downloads.fakes import data, scan  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401


@pytest.fixture
def library_db(tmp_path):
    root = tmp_path / "library" / "Manga"
    (root / "Series A").mkdir(parents=True)
    (root / "Series A" / "Series A v01 (Digital).cbz").write_bytes(data("held v01"))
    store.reset_stores()
    db = store.get_store()
    added = db.add_root(str(root), "Manga")
    scan(db)
    return db, db.get_series(added.id, "Series A").id, os.path.join(root, "Series A")


def test_found_skip_and_modes_through_the_real_adapter(qapp, library_db):
    db, sid, folder = library_db
    backend = Backend(db)
    assert has_automation(backend)
    findings = FindingStore(db)
    run = findings.start_run("schedule")
    findings.offer(NewFinding(sid, "volumes", "nyaa:" + "a" * 40, "Series A v02 (Digital)", ("2",), 300_000_000,
                              "the series is on Notify"), run)
    findings.offer(NewFinding(sid, "volumes", "nyaa:" + "b" * 40, "Series A v03 (Digital)", ("3",), None,
                              "the series is on Notify"), run)
    findings.finish_run(run, series_searched=1, found=2, sent=0, errors=0, summary="1 series searched, 0 sent, "
                                                                                   "2 waiting for you")
    tab = DownloadTab(backend, confirm=lambda p, t: True, refresh_ms=0, search_delay_ms=0)
    try:
        tab.show_group(GROUP_FOUND)
        assert sorted(r.gets_label.text() for r in tab.found.rows.values()) == ["Gets v02", "Gets v03"]
        assert {r.name_label.text() for r in tab.found.rows.values()} == {"Series A"}      # the adapter's name
        assert "1 series searched" in tab.found.run_label.text()
        first = next(iter(tab.found.rows))
        assert tab.found.skip(first)
        assert findings.get(first).status == "skipped" and len(tab.found.rows) == 1
        assert tab.group_chips[GROUP_FOUND].count == 1
        modes = AutoModes(backend)
        assert modes.detail(folder)[0] == "Notify (the default)"
        modes.apply([folder], "auto")
        assert findings.own_mode(sid) == "auto" and modes.detail(folder)[0] == "Auto"
        backend.set_default_mode("off")
        assert findings.default_mode() == "off"
    finally:
        tab.stop()
        tab.deleteLater()


def test_a_librarys_default_mode_through_the_real_adapter(qapp, library_db):
    # owner, 2026-10-13: "Can I set Automatic Downloads [Off/Notify/Auto] per library?"
    db, sid, folder = library_db
    backend = Backend(db)
    root = db.list_roots()[0]
    root.auto_mode = "off"
    db.update_root(root)
    assert backend.series_modes([sid]) == {sid: "off"} and backend.default_for(sid) == ("off", True)
    assert backend.series_defaults([sid]) == {sid: ("off", True)}
    modes = AutoModes(backend)
    assert modes.detail(folder)[0] == "Off (the library's default)"
    modes.refresh([folder])                             # off the UI thread
    wait_until(qapp, lambda: modes.mode_of(folder) is not None and not modes._running)
    assert modes.mode_of(folder) == "off" and modes.chips() == {}       # on its library's default: no chip
    modes.apply([folder], "notify")
    assert modes.chips() == {folder: "Notify"}          # its own mode differs from its library's
