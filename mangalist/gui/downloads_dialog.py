"""The Downloads list as a dialog: every download MangaList has sent to qBittorrent, with where it stands. The content
is :class:`~mangalist.gui.downloads_list.DownloadsList` (the Download tab's "In progress"); this wraps it with a Close
button for whatever still opens the list on its own.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QVBoxLayout, QWidget

from ..downloads.contracts import DownloadRecord
from .download_style import apply_style
from .downloads_backend import DownloadsBackend
from .downloads_list import DownloadsList

NameFn = Callable[[int], str]


class DownloadsDialog(QDialog):
    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None,
                 series_name: Optional[NameFn] = None, autostart: bool = True):
        super().__init__(parent)
        self.setWindowTitle("Downloads")
        self.resize(1000, 460)
        self.list = DownloadsList(backend, self, autostart=False, heading="Downloads", series_name=series_name)
        outer = QVBoxLayout(self)
        outer.addWidget(self.list, 1)
        box = QDialogButtonBox(QDialogButtonBox.Close)
        box.rejected.connect(self.reject)
        outer.addWidget(box)
        apply_style(self)
        if autostart:
            self.list.refresh()

    @property
    def records(self) -> List[DownloadRecord]:
        return self.list.records

    def done(self, result: int) -> None:
        self.list.stop()
        super().done(result)


def open_downloads_dialog(parent: Optional[QWidget], backend: DownloadsBackend,
                          series_name: Optional[NameFn] = None) -> None:
    DownloadsDialog(backend, parent, series_name).exec()
