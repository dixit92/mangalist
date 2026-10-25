"""In progress offers "Remove from qBittorrent…" on a failed torrent row (owner, 2026-10-24), asks with or without its
files, and runs it off the UI thread. Made-up names only."""

from __future__ import annotations

import dataclasses
import threading

import pytest

pytest.importorskip("PySide6")

from mangalist.downloads.contracts import DownloadStatus as S  # noqa: E402
from mangalist.gui.downloads_list import DownloadsList  # noqa: E402

from .conftest import FakeBackend, qapp, record, wait_until  # noqa: E402,F401


class RemovingBackend(FakeBackend):
    def remove_failed_download(self, record_id, *, delete_files):
        self.threads.append(threading.get_ident())
        self.removed_failed = getattr(self, "removed_failed", []) + [(record_id, delete_files)]
        i = next(i for i, r in enumerate(self.record_list) if r.id == record_id)
        self.record_list[i] = dataclasses.replace(self.record_list[i], status=S.CANCELLED)
        return self.record_list[i]


def _menu_on(dlg, row, pick=None):
    seen = []

    def answer(menu, pos):
        acts = {a.text(): a for a in menu.actions()}
        seen.append({t: a.isEnabled() for t, a in acts.items()})
        return acts.get(pick) if pick else None
    dlg._exec_menu = answer
    dlg._on_context_menu(dlg.table.visualItemRect(dlg.table.item(row, 0)).center())
    return seen[0] if seen else None


@pytest.mark.parametrize("answer", [True, False])
def test_a_failed_torrent_row_can_be_removed_from_qbittorrent(qapp, answer):
    backend = RemovingBackend(records=[record(1, status=S.FAILED, error="nothing filed")])
    dlg = DownloadsList(backend, confirm_remove_failed=lambda rec: answer)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    assert _menu_on(dlg, 0, pick="Remove from qBittorrent…") == {"Remove from qBittorrent…": True, "Dismiss": True}
    wait_until(qapp, lambda: dlg._call is None and dlg.table.rowCount() == 0)
    assert backend.removed_failed == [(1, answer)] and threading.get_ident() not in backend.threads
    assert dlg.status_label.text().startswith("Removed from qBittorrent")
    assert ("with its files" in dlg.status_label.text()) is answer


def test_saying_cancel_removes_nothing(qapp):
    backend = RemovingBackend(records=[record(1, status=S.FAILED)])
    dlg = DownloadsList(backend, confirm_remove_failed=lambda rec: None)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    _menu_on(dlg, 0, pick="Remove from qBittorrent…")
    assert dlg._call is None and not getattr(backend, "removed_failed", [])


def test_a_failed_torrent_no_longer_in_qbittorrent_offers_only_dismiss(qapp):
    backend = RemovingBackend(records=[dataclasses.replace(record(1, status=S.FAILED), in_client=False)])
    dlg = DownloadsList(backend)
    wait_until(qapp, lambda: dlg.table.rowCount() == 1)
    assert _menu_on(dlg, 0) == {"Dismiss": True}
