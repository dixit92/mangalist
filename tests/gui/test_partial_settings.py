"""Settings > Download sources: "Download only the missing volumes of a pack" (offscreen Qt, a real database)."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist import store  # noqa: E402
from mangalist.downloads.options import KEY_PARTIAL_DOWNLOADS, get_flag, load_nyaa_options  # noqa: E402
from mangalist.gui.settings_sections import SourcesPage  # noqa: E402

from .conftest import FakeBackend, qapp  # noqa: E402,F401


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def made():
    pages = []
    yield pages
    for page in pages:
        page.deleteLater()


def page_for(qapp, db, made, backend="fake"):
    page = SourcesPage(db, FakeBackend() if backend == "fake" else backend)
    made.append(page)
    return page


def test_the_default_is_on_for_a_fresh_database(qapp, db, made):
    page = page_for(qapp, db, made)
    assert get_flag(db, KEY_PARTIAL_DOWNLOADS) is True
    assert page.partial_check.isChecked() and page.partial_check.isEnabled()
    assert page.partial_check.text() == "Download only the missing volumes of a pack"
    assert "seeds only what it downloaded" in page.partial_check.toolTip()


def test_it_is_stored_as_soon_as_it_is_changed_and_read_back(qapp, db, made):
    page = page_for(qapp, db, made)
    page.partial_check.setChecked(False)
    assert get_flag(db, KEY_PARTIAL_DOWNLOADS) is False
    again = page_for(qapp, db, made)
    assert not again.partial_check.isChecked()
    again.partial_check.setChecked(True)
    assert get_flag(db, KEY_PARTIAL_DOWNLOADS) is True
    page.refresh()
    assert page.partial_check.isChecked()                                        # refresh follows the stored value


def test_loading_the_page_does_not_write_the_setting(qapp, db, made):
    page_for(qapp, db, made)
    assert db.get_setting(KEY_PARTIAL_DOWNLOADS, None) is None


def test_it_does_not_reload_the_download_tab(qapp, db, made):
    page = page_for(qapp, db, made)
    fired = []
    page.downloads_changed.connect(lambda: fired.append(1))
    page.partial_check.setChecked(False)
    assert fired == []


def test_it_is_off_limits_when_downloads_are_off(qapp, db, made):
    page = page_for(qapp, db, made, backend=None)
    assert not page.partial_check.isEnabled()


def test_the_nyaa_options_are_untouched_by_it(qapp, db, made):
    before = load_nyaa_options(db)
    page = page_for(qapp, db, made)
    page.partial_check.setChecked(False)
    assert load_nyaa_options(db) == before


def test_the_backend_reads_the_same_setting(qapp, db, made):
    from mangalist.downloads.adapter import Backend

    page = page_for(qapp, db, made)
    backend = Backend(db)
    assert backend.partial_default() is True
    page.partial_check.setChecked(False)
    assert backend.partial_default() is False
