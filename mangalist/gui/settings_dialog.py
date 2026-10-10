"""The Settings dialog: one dialog, six sections on the left (Library, Connected services, Download sources, Matching,
Automation, Logging) - the approved mockup's ``Settings.dc.html``. It absorbs the Roots, MangaPixer and qBittorrent dialogs.

:func:`open_settings` opens it modally and returns what changed (:class:`~mangalist.gui.shell.SettingsResult`) so the
shell can rescan, re-read MangaPixer's data or reload the Download tab. Switches are stored as they are changed; the
forms that hold a login (MangaPixer, qBittorrent) and the roots editor have their own Save, as before. Passwords and
tokens stay write-only.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .download_style import apply_style, set_prop
from .download_widgets import button, label
from .downloads_backend import DownloadsBackend
from .mangapixer_dialog import ClientFactory
from .roots_dialog import BrowseFn
from .settings_common import SectionPage
from .settings_library import LibraryPage
from .settings_sections import SECTION_LOGGING, AutomationPage, LoggingPage, MatchingPage, SourcesPage
from .settings_services import ServicesPage
from .shell import (
    SECTION_AUTOMATION,
    SECTION_LIBRARY,
    SECTION_MATCHING,
    SECTION_SERVICES,
    SECTION_SOURCES,
    SECTIONS,
    SettingsResult,
)

NAV = (
    (SECTION_LIBRARY, "Library", "Roots, file naming"),
    (SECTION_SERVICES, "Connected services", "MangaPixer, qBittorrent, Suwayomi"),
    (SECTION_SOURCES, "Download sources", "nyaa, Suwayomi sources"),
    (SECTION_MATCHING, "Matching", "MangaUpdates"),
    (SECTION_AUTOMATION, "Automation", "Schedules, Remove Completed"),
    (SECTION_LOGGING, "Logging", "Level, log files and folder"),
)
# The sections of this dialog: the shell's five, then Logging (kept here so the shell's list is not changed).
DIALOG_SECTIONS = (*SECTIONS, SECTION_LOGGING)


class _NavButton(QFrame):
    """A section's entry: its name over a smaller grey hint (which wraps). A frame, not a push button, so the wrapped
    hint can make the entry taller."""

    clicked = Signal()

    def __init__(self, text: str, hint: str):
        super().__init__()
        self.setProperty("nav", True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.title = label(text, "navtitle")
        self.hint = label(hint, "navhint", wrap=True)
        for w in (self.title, self.hint):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(w)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class SettingsDialog(QDialog):
    def __init__(self, parent: Optional[QWidget], db, backend: Optional[DownloadsBackend], section: Optional[str] = None,
                 browse: Optional[BrowseFn] = None, client_factory: Optional[ClientFactory] = None,
                 info: Optional[Callable[[QWidget, str, str], None]] = None, cache=None, env=None):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("Settings")
        self.resize(1080, 760)
        self._db = db
        self._backend = backend
        self._flags = {"roots": False, "mangapixer": False, "downloads": False}
        if cache is None:
            try:
                from ..services.mangapixer import open_cache

                cache = open_cache(db)
            except Exception:  # noqa: BLE001 - MangaPixer is optional
                cache = None
        self.cache = cache

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QFrame()
        head.setObjectName("settingsHeader")
        head.setFixedHeight(56)
        hl = QHBoxLayout(head)
        hl.setContentsMargins(20, 0, 20, 0)
        hl.addWidget(label("Settings", "h2"))
        hl.addStretch(1)
        self.btn_close = button("Close")
        self.btn_close.clicked.connect(self.accept)
        hl.addWidget(self.btn_close)
        outer.addWidget(head)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        nav = QFrame()
        nav.setObjectName("settingsNav")
        nav.setFixedWidth(230)
        nv = QVBoxLayout(nav)
        nv.setContentsMargins(12, 12, 12, 12)
        nv.setSpacing(2)
        self._nav: Dict[str, _NavButton] = {}
        for key, title, hint in NAV:
            btn = _NavButton(title, hint)
            btn.clicked.connect(lambda _=False, k=key: self.show_section(k))
            nv.addWidget(btn)
            self._nav[key] = btn
        nv.addStretch(1)
        body.addWidget(nav)

        self.stack = QStackedWidget()
        self.pages: Dict[str, SectionPage] = {
            SECTION_LIBRARY: LibraryPage(db, browse=browse, cache=cache),
            SECTION_SERVICES: ServicesPage(db, backend, cache, client_factory=client_factory, info=info),
            SECTION_SOURCES: SourcesPage(db, backend),
            SECTION_MATCHING: MatchingPage(db),
            SECTION_AUTOMATION: AutomationPage(db, backend, cache, env=env),
            SECTION_LOGGING: LoggingPage(db, env=env),
        }
        for key in DIALOG_SECTIONS:
            page = self.pages[key]
            scroll = QScrollArea()
            scroll.setObjectName("settingsScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            holder = QWidget()
            holder.setObjectName("settingsBody")
            hv = QVBoxLayout(holder)
            hv.setContentsMargins(28, 22, 28, 22)
            hv.addWidget(page)
            scroll.setWidget(holder)
            self.stack.addWidget(scroll)
            page.roots_changed.connect(lambda: self._flag("roots"))
            page.mangapixer_changed.connect(lambda: self._flag("mangapixer"))
            page.downloads_changed.connect(lambda: self._flag("downloads"))
            page.section_requested.connect(self.show_section)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        apply_style(self)
        self.show_section(section if section in DIALOG_SECTIONS else SECTION_LIBRARY)

    def _flag(self, name: str) -> None:
        self._flags[name] = True

    def show_section(self, section: str) -> None:
        if section not in self.pages:
            return
        self.current = section
        self.stack.setCurrentIndex(DIALOG_SECTIONS.index(section))
        for key, btn in self._nav.items():
            set_prop(btn, "current", key == section)
        self.pages[section].on_show()

    def result_data(self) -> SettingsResult:
        return SettingsResult(roots_changed=self._flags["roots"], mangapixer_changed=self._flags["mangapixer"],
                              downloads_changed=self._flags["downloads"])

    def done(self, result: int) -> None:
        for page in self.pages.values():
            page.stop()
        super().done(result)


def open_settings(parent: Optional[QWidget], db, backend: Optional[DownloadsBackend],
                  section: Optional[str] = None) -> SettingsResult:
    """Open the Settings dialog (modal) and return what changed. *backend* is None where downloads are off: the
    qBittorrent card and the download sources then say so."""
    dlg = SettingsDialog(parent, db, backend, section)
    dlg.exec()
    result = dlg.result_data()
    dlg.deleteLater()
    return result


__all__ = ["SettingsDialog", "open_settings", "NAV", "DIALOG_SECTIONS", "SECTION_LOGGING"]
