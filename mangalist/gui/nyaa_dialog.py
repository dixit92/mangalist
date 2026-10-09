"""Find volumes on nyaa, as a dialog: the missing volumes of one series, the folder they will be filed into, and the
releases nyaa has, best first. The content is :class:`~mangalist.gui.releases_panel.ReleasesPanel` (the Download tab shows
the same panel); this dialog runs the search itself and adds a Close button.

The Download tab replaces it in the redesigned window; it stays for whatever still opens a single series' search.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from ..downloads.contracts import DownloadRecord
from .download_style import apply_style
from .downloads_backend import DownloadsBackend
from .releases_panel import ConfirmFn, ReleasesPanel, volumes_text, wanted_volumes_for
from .volumes_target import VolumeTarget


class NyaaDialog(QDialog):
    def __init__(self, backend: DownloadsBackend, target: VolumeTarget, parent: Optional[QWidget] = None,
                 confirm: Optional[ConfirmFn] = None, open_url: Optional[Callable[[str], object]] = None,
                 autostart: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Find volumes on nyaa")
        self.resize(1100, 600)
        self.target = target
        self.panel = ReleasesPanel(backend, self, confirm=confirm, open_url=open_url)
        outer = QVBoxLayout(self)
        outer.addWidget(self.panel, 1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        outer.addWidget(box)
        apply_style(self)
        if autostart:
            self.start()

    def start(self) -> None:
        """Look up the target folder and run the search (both off the UI thread)."""
        self.panel.open_target(self.target)

    @property
    def sent_records(self) -> List[DownloadRecord]:
        return self.panel.sent_records

    def done(self, result: int) -> None:
        self.panel.stop()
        super().done(result)


def open_nyaa_dialog(parent: Optional[QWidget], backend: DownloadsBackend, target: VolumeTarget) -> NyaaDialog:
    """Open the find-volumes dialog (modal); returns it so the caller can read ``sent_records``."""
    dlg = NyaaDialog(backend, target, parent)
    dlg.exec()
    return dlg


__all__ = ["NyaaDialog", "open_nyaa_dialog", "volumes_text", "wanted_volumes_for"]
