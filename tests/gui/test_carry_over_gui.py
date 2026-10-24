"""Settings > Connected services > Built-in extensions > "Carry over from Suwayomi" (offscreen Qt, 2026-10-23): the card's
line and button (only while Suwayomi is set up and series have a source there), the check first (a dry run: what it
will do, nothing written), Carry over with progress, the report, a second run with nothing left. The REAL GUI backend
and host client talk to the in-process fake host; Suwayomi is the in-memory fake. Buttons only (iPad)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist import store  # noqa: E402
from mangalist.downloads.adapter import Backend  # noqa: E402
from mangalist.downloads.contracts import SuwayomiConnection  # noqa: E402
from mangalist.gui.downloads_backend import BackendError  # noqa: E402
from mangalist.gui.settings_services import ServicesPage  # noqa: E402
from mangalist.services.extensions import carry_over as co  # noqa: E402
from mangalist.services.extensions.client import ExtensionHostClient  # noqa: E402
from mangalist.store.downloads import ExtensionHostConfig, SuwayomiSettings  # noqa: E402

from ..services.extensions.carry_fakes import MANGADEX, WEEB, FakeSuwayomiCarry, raw_choice, set_suwayomi_choice  # noqa: E402
from ..services.extensions.fake_host import MANGADEX_EN, MANGADEX_PKG, WEEB_ID, WEEB_PKG, FakeHost  # noqa: E402
from .conftest import qapp, wait_until  # noqa: E402,F401


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "exthost").start()
    yield h
    h.stop()


@pytest.fixture
def backend(db, host):
    b = Backend(db)
    b.save_extension_host(ExtensionHostConfig(url=host.url))
    return b


@pytest.fixture
def suwayomi():
    s = FakeSuwayomiCarry()
    s.add(11, MANGADEX, "/manga/md-1", "Example Manga")
    s.add(12, WEEB, "/series/weeb-1", "Weeb Story")
    return s


def runner_for(db, suwayomi, host, calls=None):
    def run(dry_run, progress):
        if calls is not None:
            calls.append(dry_run)
        client = ExtensionHostClient(host.url, timeout=10)
        try:
            return co.carry_over(db, suwayomi, client, dry_run=dry_run, progress=progress, pause=0)
        finally:
            client.close()

    return run


def set_up(db):
    SuwayomiSettings(db).save_connection(SuwayomiConnection(base_url="http://suwayomi.example:4567"))
    set_suwayomi_choice(db, 1, 11, MANGADEX_EN, "Example Manga", "mangadex-id")
    set_suwayomi_choice(db, 2, 12, WEEB_ID, "Weeb Story", "confirmed")
    set_suwayomi_choice(db, 3, 99, MANGADEX_EN, "Gone", "mangadex-id")


def idle(qapp, panel):
    wait_until(qapp, lambda: not panel.busy())


def test_the_card_offers_it_only_while_suwayomi_has_series_to_carry(qapp, db, backend, host):
    page = ServicesPage(db, backend, None, info=lambda *a: None)
    assert not page.carry_row.isVisibleTo(page)                     # Suwayomi not set up
    SuwayomiSettings(db).save_connection(SuwayomiConnection(base_url="http://suwayomi.example:4567"))
    page.refresh()
    assert not page.carry_row.isVisibleTo(page)                     # no series picked in Suwayomi
    set_up(db)
    page.refresh()
    assert page.carry_row.isVisibleTo(page) and page.btn_carry_over.text() == "Carry over from Suwayomi"
    assert "3 of 3 series" in page.carry_label.text()
    page.choose_backend("extensions")                               # also once the built-in extensions are chosen
    assert page.carry_row.isVisibleTo(page)
    page.stop()


def test_check_confirm_carry_over_and_report(qapp, db, backend, host, suwayomi):
    set_up(db)
    calls = []
    page = ServicesPage(db, backend, None, info=lambda *a: None, carry_runner=runner_for(db, suwayomi, host, calls))
    changed = []
    page.downloads_changed.connect(lambda: changed.append(1))
    page.btn_carry_over.click()
    panel = page.carry_panel
    assert panel is not None and page.stack.currentWidget() is page.editor_page
    idle(qapp, panel)
    # the check: what it will do, nothing done yet
    assert calls == [True]
    plan = panel.status.text()
    assert "install 2 extension(s)" in plan and "MangaDex (2 series)" in plan and "carry over 2 series" in plan
    assert "1 Suwayomi does not know the manga" in plan
    assert "Gone" in panel.details.toPlainText()
    assert host.installed == {} and raw_choice(db, 1) is None
    assert panel.btn_carry.isEnabled() and panel.btn_carry.text() == "Carry over 2 series"
    # confirmed: done, with the report
    panel.btn_carry.click()
    assert panel.busy() and not panel.btn_carry.isEnabled() and not panel.btn_check.isEnabled()
    idle(qapp, panel)
    assert calls == [True, False]
    assert set(host.installed) == {MANGADEX_PKG, WEEB_PKG}
    assert raw_choice(db, 1)["manga_id"] == host.manga_ids[(MANGADEX_EN, "/manga/md-1")]
    assert raw_choice(db, 2)["how"] == "confirmed"
    assert panel.status.text().startswith("Done. Carried 2, Suwayomi does not know the manga 1.")
    assert "Extensions installed: MangaDex" in panel.details.toPlainText()
    assert not panel.btn_carry.isEnabled() and panel.btn_carry.text() == "Carried over" and changed
    # Check again: nothing left to do
    panel.btn_check.click()
    idle(qapp, panel)
    assert "Nothing to carry over." in panel.status.text() and "2 series are already set up" in panel.status.text()
    assert not panel.btn_carry.isEnabled()
    assert panel.carry() is False
    # back on the cards: the line says what is left
    page.close_editor()
    assert "1 of 3 series" in page.carry_label.text()
    page.stop()


def test_a_failing_check_says_why_and_carries_nothing(qapp, db, backend, host):
    set_up(db)

    def broken(dry_run, progress):
        raise BackendError("Suwayomi could not be asked: Suwayomi could not be reached at http://suwayomi.example:4567")

    page = ServicesPage(db, backend, None, info=lambda *a: None, carry_runner=broken)
    panel = page.open_carry_over()
    idle(qapp, panel)
    assert panel.status.text().startswith("Not carried over: Suwayomi could not be asked")
    assert not panel.btn_carry.isEnabled() and panel.btn_check.isEnabled() and panel.carry() is False
    page.stop()


def test_the_default_runner_turns_a_missing_suwayomi_into_a_message(qapp, db, backend, host):
    from mangalist.gui.carry_over_panel import default_runner

    with pytest.raises(BackendError, match="Suwayomi is not set up"):
        default_runner(db)(True, lambda *a: None)


def test_the_panel_carries_back_to_suwayomi(qapp, db, suwayomi, host):
    """Transition audit B8: "Carry back to Suwayomi…" turns the panel around - check, confirm, carry back, report."""
    import json

    from mangalist.gui.carry_over_panel import CarryOverPanel
    from mangalist.store.downloads import META_EXTENSIONS_SERIES, META_SUWAYOMI_SERIES

    host.installed[MANGADEX_PKG] = "1.4.200"
    mid = ExtensionHostClient(host.url).register_manga(MANGADEX_EN, "/manga/md-1", "Example Manga")["id"]
    db.set_meta(f"{META_EXTENSIONS_SERIES}5", json.dumps({"source_id": MANGADEX_EN, "manga_id": mid,
                                                          "manga_url": "/manga/md-1", "how": "confirmed"}))

    def back(dry_run, progress):
        client = ExtensionHostClient(host.url, timeout=10)
        try:
            return co.carry_back(db, suwayomi, client, dry_run=dry_run, progress=progress, pause=0)
        finally:
            client.close()

    panel = CarryOverPanel(db, runner=lambda dry_run, progress: co.CarryReport(dry_run=dry_run), back_runner=back)
    wait_until(qapp, lambda: not panel.busy())
    assert panel.btn_mode.text() == "Carry back to Suwayomi…"
    assert panel.toggle_mode()
    wait_until(qapp, lambda: not panel.busy())
    assert "give 1 series the manga chosen on the host" in panel.status.text()
    assert panel.btn_carry.isEnabled() and panel.btn_carry.text() == "Carry back 1 series"
    assert db.get_meta(f"{META_SUWAYOMI_SERIES}5") is None                      # the check wrote nothing
    assert panel.carry()
    wait_until(qapp, lambda: not panel.busy())
    assert panel.status.text().startswith("Done. Carried back 1")
    assert json.loads(db.get_meta(f"{META_SUWAYOMI_SERIES}5"))["manga_id"] == 11
    assert panel.btn_mode.text() == "Carry over from Suwayomi…"
    panel.stop()
