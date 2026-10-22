"""The main window reads the series' creators from MangaUpdates in the background after a MangaUpdates lookup (offscreen
Qt, a fake MangaUpdates, no network): what it asks, that the ``creator:`` filter finds them afterwards, and when it does
not start."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist import creator_names as cn  # noqa: E402
from mangalist.gui import background as bg  # noqa: E402
from mangalist.gui import lanes  # noqa: E402

from .conftest import qapp  # noqa: E402,F401
from .test_auto_modes_window import _backend, _sync, make_window  # noqa: E402,F401


class MangaUpdates:
    def __init__(self):
        self.calls = []

    def get_series(self, sid):
        self.calls.append(("series", sid))
        return {"authors": [{"name": f"Writer {sid}", "author_id": 50 + sid, "type": "Author"}]}

    def get_author(self, aid):
        self.calls.append(("author", aid))
        return {"name": f"Writer {aid - 50}", "associated": [{"name": f"Pen Name {aid - 50}"}]}


@pytest.fixture
def mu(monkeypatch):
    fake = MangaUpdates()
    monkeypatch.setattr(lanes, "creator_name_clients", lambda: (fake.get_series, fake.get_author, lambda e: False))
    monkeypatch.setattr(cn.time, "sleep", lambda s: None)
    monkeypatch.setattr(bg, "start_call", _sync)
    return fake


def start(win):
    """The lookup, run at once (the test runner returns its handle after the call has finished: the window thinks it
    still runs)."""
    win._start_creator_names()
    win._creator_call = None


def number_series(win, ids):
    for i, mu_id in enumerate(ids):
        win._model.entry_at(i).mu_id = mu_id


def test_the_creators_are_read_and_the_filter_finds_them(make_window, mu):
    win = make_window(_backend())
    number_series(win, [101, 102])
    start(win)
    assert mu.calls == [("series", 101), ("series", 102), ("author", 151), ("author", 152)]
    assert win._model.creator_index().known(101)
    win._proxy.set_text_filter('creator:"pen name 102"')
    titles = [win._proxy.data(win._proxy.index(r, 2)) for r in range(win._proxy.rowCount())]
    assert titles == ["Beta Days"]


def test_what_is_cached_is_not_asked_again(make_window, mu):
    win = make_window(_backend())
    number_series(win, [101, 102])
    start(win)
    mu.calls.clear()
    start(win)
    assert mu.calls == []


def test_nothing_is_asked_without_a_numbered_series_or_a_client(make_window, mu, monkeypatch):
    win = make_window(_backend())
    win._start_creator_names()                                  # no series has a MangaUpdates id
    assert mu.calls == []
    number_series(win, [101])
    monkeypatch.setattr(lanes, "creator_name_clients", lambda: None)
    start(win)
    assert mu.calls == []


def test_it_starts_when_a_mangaupdates_lookup_ends_but_not_when_it_was_stopped(make_window, mu):
    win = make_window(_backend())
    number_series(win, [101])

    class Worker:
        def __init__(self, aborted):
            self.aborted = aborted

    win._mu_worker = Worker(aborted=True)
    win._on_mu_thread_finished()
    win._creator_call = None
    assert mu.calls == []
    win._mu_worker = Worker(aborted=False)
    win._on_mu_thread_finished()
    assert mu.calls == [("series", 101), ("author", 151)]


def test_the_download_tab_gets_the_creators_too(make_window, mu):
    win = make_window(_backend())
    number_series(win, [101])
    start(win)
    assert win._download_tab is not None and win._download_tab._creators.known(101)


def test_series_mangapixer_knows_are_never_asked_for_and_unknown_ones_still_are(make_window, mu):
    from mangalist.knowledge import LINK_CONFIRMED, SOURCE_MANGAPIXER, SeriesKnowledge

    win = make_window(_backend())
    number_series(win, [101, 102, 103])                         # Alpha Saga, Beta Days, Gamma Tales
    with_creators = SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="101", title="Alpha Saga",
                                    creators=(("Exported Writer", ("Pen Name",)),))
    without = SeriesKnowledge(source=SOURCE_MANGAPIXER, link_state=LINK_CONFIRMED, mu_id="102", title="Beta Days")
    known = {"Alpha Saga": with_creators, "Beta Days": without}
    win._knowledge_for = lambda e: known.get(e.title)
    win._model.set_state_providers(knowledge_for=win._knowledge_for)
    start(win)
    assert mu.calls == [("series", 103), ("author", 153)]       # only the series MangaPixer does not know
    win._proxy.set_text_filter('creator:"exported writer"')
    assert [win._proxy.data(win._proxy.index(r, 2)) for r in range(win._proxy.rowCount())] == []   # (its state is not computed yet)
    win._model.state_at(0)
    win._proxy.refresh_scope()
    assert [win._proxy.data(win._proxy.index(r, 2)) for r in range(win._proxy.rowCount())] == ["Alpha Saga"]
    win._proxy.set_text_filter('creator:"writer 103"')
    assert [win._proxy.data(win._proxy.index(r, 2)) for r in range(win._proxy.rowCount())] == ["Gamma Tales"]
