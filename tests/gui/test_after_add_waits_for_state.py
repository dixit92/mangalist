"""Add series' "Find releases now" waits for the new series' state (computed in the background after the scan,
UI performance stage 2): a find started before it searched nothing - "0 series searched" (owner, 2026-10-15)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from mangalist.gui.main_window import MainWindow  # noqa: E402


class _Model:
    def __init__(self, folders, pending):
        self.folders, self.pending = folders, pending

    def rowCount(self):
        return len(self.folders)

    def entry_at(self, row):
        return SimpleNamespace(folder=self.folders[row])

    def states_pending(self, rows):
        return self.pending and bool(list(rows))


def _window(pending):
    finds, missing, tabs = [], [], []
    tab = SimpleNamespace(find_for=lambda ids: finds.append(list(ids)) or True)
    win = SimpleNamespace(_after_add=("find", "/lib/Made Up Series", 7), _download_tab=tab,
                          _model=_Model(["/lib/Other", "/lib/Made Up Series"], pending),
                          _rebuild_wanted=lambda: None, show_tab=tabs.append, get_missing=missing.append)
    return win, finds, missing


def test_the_series_panel_waits_while_the_new_series_state_is_computed():
    """Until its state is in, the new series is in no wanted list: its panel would have nothing to show."""
    win, finds, missing = _window(pending=True)
    MainWindow._run_after_add(win)
    assert finds == [] and missing == [] and win._after_add is not None          # kept for the states' push
    win._model.pending = False                                                   # the background states arrived
    MainWindow._run_after_add(win)
    assert missing == ["/lib/Made Up Series"] and finds == [] and win._after_add is None   # its panel, no bulk find
    MainWindow._run_after_add(win)                                               # runs once
    assert missing == ["/lib/Made Up Series"]


def test_a_series_the_scan_did_not_list_still_opens_its_missing_list():
    win, finds, missing = _window(pending=True)
    win._after_add = ("open", "/lib/Not Scanned", 9)
    MainWindow._run_after_add(win)
    assert missing == ["/lib/Not Scanned"] and win._after_add is None
