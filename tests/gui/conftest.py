"""Fakes for the volumes GUI tests: a backend with canned answers, and release / record factories. Made-up
titles and paths only; nothing here touches a network, a qBittorrent or a library."""

from __future__ import annotations

import threading
import time
from typing import List, Optional, Sequence

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from mangalist.downloads.contracts import DownloadRecord, DownloadStatus, NyaaCandidate, Placement  # noqa: E402
from mangalist.gui.downloads_backend import BackendError, QbtSettings  # noqa: E402
from mangalist.gui.volumes_target import VolumeTarget  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def wait_until(qapp, predicate, timeout: float = 10.0) -> None:
    """Spin the event loop until ``predicate()`` (worker threads deliver their results through it)."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the GUI")
        qapp.processEvents()
        time.sleep(0.005)
    qapp.processEvents()


def settle_states(win, timeout: float = 10.0) -> None:
    """Wait until the window's background has computed every row's state and the counts and To get followed (UI
    performance stage 2: with the real runner, the states are pushed from a worker thread)."""
    from PySide6.QtWidgets import QApplication

    wait_until(QApplication.instance(), lambda: not win._model.states_pending() and win._states_call is None, timeout)


def candidate(title="Example Series v03-05 (Digital) (Group)", *, info_hash="a" * 40, vol_from="3", vol_to="5",
              covers_missing=("3", "4", "5"), covers_held=(), digital=True, group="Group", seeders=12, trusted=True,
              not_comic=False, is_pack=None, reasons=("Digital release", "Covers 3 missing volumes")) -> NyaaCandidate:
    return NyaaCandidate(
        title=title, view_url=f"https://nyaa.example/view/{info_hash[:6]}", torrent_url="https://nyaa.example/dl/x",
        info_hash=info_hash, size_bytes=734_003_200, seeders=seeders, leechers=1, downloads=40, trusted=trusted,
        remake=False, published="2026-09-30T12:00:00+00:00", category="3_1", vol_from=vol_from, vol_to=vol_to,
        digital=digital, group=group, is_pack=(vol_from != vol_to) if is_pack is None else is_pack, not_comic=not_comic, covers_missing=tuple(covers_missing),
        covers_held=tuple(covers_held), rank=1.0, reasons=tuple(reasons))


def record(id=1, series_id=7, status=DownloadStatus.SENT, wanted=("3", "4", "5"), error=None, copied=False,
           title="Example Series v03-05") -> DownloadRecord:
    return DownloadRecord(id=id, series_id=series_id, info_hash=f"{id:040x}", title=title, wanted_volumes=tuple(wanted),
                          target_dir="/lib/Example Series", status=status, created_at="2026-10-07T10:00:00+00:00",
                          updated_at="2026-10-07T10:05:00+00:00", copied=copied, error=error)


def target(**kw) -> VolumeTarget:
    base = dict(series_id=7, folder="/lib/Example Series", title="Example Series",
                titles=("Example Series", "Exemplar"), missing=("3", "4", "5", "9"), held=("1", "2"))
    base.update(kw)
    return VolumeTarget(**base)


class FakeBackend:
    """Canned answers; every call is recorded (with the thread it ran on) so tests can assert on them."""

    def __init__(self, *, results: Optional[Sequence[NyaaCandidate]] = None, placement: Optional[Placement] = None,
                 records: Sequence[DownloadRecord] = (), series_ids=None):
        self.results = list(results if results is not None else [candidate()])
        self.placement_answer = placement or Placement("/lib/Example Series", "/lib/Example Series",
                                                       "volumes live in the series folder")
        self.record_list: List[DownloadRecord] = list(records)
        self.series_ids = series_ids if series_ids is not None else {}
        self.search_error: Optional[str] = None
        self.send_error: Optional[str] = None
        self.placement_error: Optional[str] = None
        self.test_error: Optional[str] = None
        self.settings = QbtSettings(base_url="http://qbt.example:8080", username="owner", has_password=True,
                                    verify_tls=False, save_path="/data/appdata/torrents/mangalist",
                                    remove_completed=True)
        self.saved: List[tuple] = []
        self.tested: List[tuple] = []
        self.searched: List[tuple] = []
        self.sent: List[tuple] = []
        self.threads: List[int] = []
        self.gate: Optional[threading.Event] = None          # set to hold a search until released
        self.checks = 0
        self.check_error: Optional[str] = None

    def series_id_for(self, folder):
        return self.series_ids.get(folder)

    def placement(self, series_id):
        self.threads.append(threading.get_ident())
        if self.placement_error:
            raise BackendError(self.placement_error)
        return self.placement_answer

    def search(self, titles, missing, held):
        self.threads.append(threading.get_ident())
        self.searched.append((tuple(titles), tuple(missing), tuple(held)))
        if self.gate is not None:
            self.gate.wait(10)
        if self.search_error:
            raise BackendError(self.search_error)
        return list(self.results)

    def send(self, series_id, cand, wanted_volumes, target_dir):
        self.threads.append(threading.get_ident())
        if self.send_error:
            raise BackendError(self.send_error)
        self.sent.append((series_id, cand.info_hash, tuple(wanted_volumes), target_dir))
        rec = record(id=len(self.sent), series_id=series_id, wanted=wanted_volumes, title=cand.title)
        self.record_list.append(rec)
        return rec

    def records(self, series_id=None):
        self.threads.append(threading.get_ident())
        return [r for r in self.record_list if series_id is None or r.series_id == series_id]

    def load_settings(self):
        return self.settings

    def save_settings(self, settings, password):
        self.saved.append((settings, password))
        self.settings = QbtSettings(**{**settings.__dict__, "has_password": settings.has_password or bool(password)})

    def test_connection(self, settings, password):
        self.threads.append(threading.get_ident())
        self.tested.append((settings, password))
        if self.test_error:
            raise BackendError(self.test_error)
        return "v5.2.4"

    def remove_now(self, record_id):
        """Remove now: the record becomes REMOVED (the volumes cycle's row action)."""
        from dataclasses import replace as _replace

        self.threads.append(threading.get_ident())
        self.removed_now = getattr(self, "removed_now", []) + [record_id]
        i = next(i for i, r in enumerate(self.record_list) if r.id == record_id)
        self.record_list[i] = _replace(self.record_list[i], status=DownloadStatus.REMOVED,
                                       error="removed by you (Remove now)")
        return self.record_list[i]

    def dismiss_downloads(self, record_ids):
        """Dismiss: FAILED records become CANCELLED (the In progress list's row action, 2026-10-15)."""
        from dataclasses import replace as _replace

        self.threads.append(threading.get_ident())
        self.dismissed = getattr(self, "dismissed", []) + [tuple(record_ids)]
        n = 0
        for i, r in enumerate(self.record_list):
            if r.id in record_ids and r.status == DownloadStatus.FAILED:
                self.record_list[i] = _replace(r, status=DownloadStatus.CANCELLED, error="dismissed by the owner")
                n += 1
        return n

    def retry_chapter_downloads(self, record_ids):
        """Retry (2026-10-20): stuck chapter records go back to waiting for Suwayomi."""
        from dataclasses import replace as _replace

        self.threads.append(threading.get_ident())
        self.retried = getattr(self, "retried", []) + [tuple(record_ids)]
        n = 0
        for i, r in enumerate(self.record_list):
            if r.id in record_ids and r.status == DownloadStatus.SENT:
                self.record_list[i] = _replace(r, error="retried by you; waiting for Suwayomi")
                n += 1
        return n

    def cancel_chapter_downloads(self, record_ids):
        """Cancel (2026-10-20): stuck chapter records become CANCELLED."""
        from dataclasses import replace as _replace

        self.threads.append(threading.get_ident())
        self.cancelled = getattr(self, "cancelled", []) + [tuple(record_ids)]
        n = 0
        for i, r in enumerate(self.record_list):
            if r.id in record_ids and r.status == DownloadStatus.SENT:
                self.record_list[i] = _replace(r, status=DownloadStatus.CANCELLED, error="cancelled by you")
                n += 1
        return n

    def check_now(self):
        self.threads.append(threading.get_ident())
        self.checks += 1
        if self.check_error:
            raise BackendError(self.check_error)
        return "torrents: 1 checked: 1 filed, 0 removed, 0 failed, 0 waiting"
