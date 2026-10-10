"""The window's top bar (mockup header): the app icon and name, the List / Download tabs (Download with its "To get"
count), the library status (roots, last scan, MangaPixer sync), Missing series (only when there are any), Rescan (with
an arrow that rescans one library folder) and Settings."""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QWidget

from . import theme
from .app_icon import render_app_icon
from .chips import TabButton
from .library_picker import LibraryPicker

ALL_LIBRARIES = "All libraries"

TAB_LIST = 0
TAB_DOWNLOAD = 1
HEIGHT = 60

# The mockup's gear (Feather's "settings" icon), stroked in the ink colour.
_GEAR_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" '
    f'stroke="{theme.INK}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="3"/><path d="'
    'M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 '
    '1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0'
    ' 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 '
    '0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65'
    ' 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0'
    ' 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65'
    ' 0 0 0-1.51 1Z'
    '"/></svg>'
)


def gear_icon() -> QIcon:
    icon = QIcon()
    renderer = QSvgRenderer(QByteArray(_GEAR_SVG.encode("utf-8")))
    for size in (18, 36):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
        icon.addPixmap(pm)
    return icon


class TopBar(QWidget):
    tab_changed = Signal(int)           # TAB_LIST / TAB_DOWNLOAD
    rescan_clicked = Signal()           # every library folder
    rescan_root_clicked = Signal(int)   # one library folder (its root id)
    settings_clicked = Signal()
    library_changed = Signal(object)    # the Library picker: a root id, or None (All libraries)
    missing_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 0, 20, 0)
        row.setSpacing(24)

        brand = QHBoxLayout()
        brand.setSpacing(10)
        icon = QLabel()
        pm = render_app_icon(56)
        pm.setDevicePixelRatio(2.0)
        icon.setPixmap(pm)
        icon.setFixedSize(28, 28)
        name = QLabel("MangaList")
        name.setProperty("role", "brand")
        brand.addWidget(icon)
        brand.addWidget(name)
        row.addLayout(brand)

        nav = QHBoxLayout()
        nav.setSpacing(4)
        self.tab_list = TabButton("List")
        self.tab_download = TabButton("Download")
        self.tab_list.setChecked(True)
        self._tabs = QButtonGroup(self)
        self._tabs.setExclusive(True)
        for i, tab in enumerate((self.tab_list, self.tab_download)):
            self._tabs.addButton(tab, i)
            nav.addWidget(tab)
        self._tabs.idClicked.connect(self.tab_changed)
        self.tab_download.setVisible(False)
        row.addLayout(nav)
        row.addStretch(1)

        # The Library picker applies to both tabs, so it lives here, next to what was scanned (owner, 2026-10-09: the
        # Download tab following the List's picker was not clear). Hidden with fewer than two library folders.
        self.library_label = QLabel("Library")
        self.library_label.setProperty("role", "status")
        self.library_picker = LibraryPicker()
        self.library_picker.setObjectName("libraryPicker")
        self.library_picker.setAccessibleName("Library")
        self.library_picker.setToolTip("Show one library folder, or all of them - in the List and the Download tab")
        self.library_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.library_picker.setMinimumWidth(150)
        self.library_picker.addItem(ALL_LIBRARIES, None)
        self.library_picker.activated.connect(lambda i: self.library_changed.emit(self.library_picker.itemData(i)))
        lib = QHBoxLayout()
        lib.setSpacing(8)
        lib.addWidget(self.library_label)
        lib.addWidget(self.library_picker)
        row.addLayout(lib)
        self._show_picker(False)

        self.status = QLabel("")
        self.status.setProperty("role", "status")
        self.status.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        row.addWidget(self.status)

        right = QHBoxLayout()
        right.setSpacing(8)
        self.btn_missing = QPushButton("Missing (0)")
        self.btn_missing.setProperty("variant", "missing")
        self.btn_missing.setToolTip("Series whose folder vanished and could not be recognised elsewhere: re-attach them "
                                    "to their new folder, or forget them")
        self.btn_missing.clicked.connect(self.missing_clicked)
        self.btn_missing.setVisible(False)
        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.setToolTip("Scan every library folder again")
        self.btn_rescan.clicked.connect(self.rescan_clicked)
        # The arrow beside it: "All libraries" / each library folder. Shown only when there is more than one.
        self.btn_rescan_menu = QPushButton("▾")
        self.btn_rescan_menu.setProperty("variant", "icon")
        self.btn_rescan_menu.setToolTip("Rescan one library folder")
        self.btn_rescan_menu.setAccessibleName("Rescan one library folder")
        self.btn_rescan_menu.setVisible(False)
        self.rescan_menu = QMenu(self.btn_rescan_menu)
        self.btn_rescan_menu.clicked.connect(self._open_rescan_menu)
        self.btn_settings = QPushButton()
        self.btn_settings.setProperty("variant", "icon")
        self.btn_settings.setIcon(gear_icon())
        self.btn_settings.setIconSize(QSize(18, 18))
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.setAccessibleName("Settings")
        self.btn_settings.clicked.connect(self.settings_clicked)
        rescan = QHBoxLayout()
        rescan.setSpacing(4)
        rescan.addWidget(self.btn_rescan)
        rescan.addWidget(self.btn_rescan_menu)
        right.addWidget(self.btn_missing)
        right.addLayout(rescan)
        right.addWidget(self.btn_settings)
        row.addLayout(right)

    # ------------------------------------------------------------------

    def set_download_available(self, available: bool) -> None:
        self.tab_download.setVisible(available)
        if not available and self.tab_download.isChecked():
            self.set_current(TAB_LIST)

    def set_download_count(self, count: Optional[int]) -> None:
        self.tab_download.set_badge(count)

    def set_current(self, tab: int) -> None:
        (self.tab_download if tab == TAB_DOWNLOAD else self.tab_list).setChecked(True)

    def current(self) -> int:
        return TAB_DOWNLOAD if self.tab_download.isChecked() else TAB_LIST

    # --- the Library picker ------------------------------------------------------------------------------

    def _show_picker(self, shown: bool) -> None:
        self.library_label.setVisible(shown)
        self.library_picker.setVisible(shown)

    def picker_shown(self) -> bool:
        return not self.library_picker.isHidden()

    def set_libraries(self, roots: Sequence[Tuple[int, str, str]], current: Optional[int] = None) -> None:
        """The picker's items from ``(root id, name, path)`` of every library folder; *current* (a root id, or None for
        all) is picked again when it is still there. Nothing is emitted. One library folder: no picker."""
        picker = self.library_picker
        picker.blockSignals(True)
        picker.clear()
        picker.addItem(ALL_LIBRARIES, None)
        for root_id, name, path in roots:
            picker.addItem(name, root_id)
            picker.setItemData(picker.count() - 1, path, Qt.ItemDataRole.ToolTipRole)
        found = picker.findData(current) if current is not None else 0
        picker.setCurrentIndex(found if found >= 0 else 0)
        picker.blockSignals(False)
        self._show_picker(len(roots) > 1)

    def current_library(self) -> Optional[int]:
        return self.library_picker.currentData()

    def library_name(self) -> str:
        """What the picker shows now ("All libraries" or the root's name)."""
        return self.library_picker.currentText()

    def set_library(self, root_id: Optional[int], emit: bool = False) -> None:
        index = self.library_picker.findData(root_id) if root_id is not None else 0
        self.library_picker.blockSignals(True)
        self.library_picker.setCurrentIndex(index if index >= 0 else 0)
        self.library_picker.blockSignals(False)
        if emit:
            self.library_changed.emit(self.current_library())

    def set_status(self, text: str, tooltip: str = "") -> None:
        self.status.setText(text)
        self.status.setToolTip(tooltip)

    def set_rescan_targets(self, roots: Sequence[Tuple[int, str, str]]) -> None:
        """The Rescan arrow's menu from ``(root id, name, path)`` of every library folder."""
        self.rescan_menu.clear()
        everything = self.rescan_menu.addAction("All libraries")
        everything.triggered.connect(lambda _c=False: self.rescan_clicked.emit())
        self.rescan_menu.addSeparator()
        for root_id, name, path in roots:
            act = self.rescan_menu.addAction(name)
            act.setToolTip(path)
            act.setData(root_id)
            act.triggered.connect(lambda _c=False, rid=root_id: self.rescan_root_clicked.emit(rid))
        self.btn_rescan_menu.setVisible(len(roots) > 1)

    def _open_rescan_menu(self) -> None:
        self.rescan_menu.setToolTipsVisible(True)
        self.rescan_menu.popup(self.btn_rescan_menu.mapToGlobal(self.btn_rescan_menu.rect().bottomLeft()))

    def set_scanning(self, scanning: bool) -> None:
        """Rescan (and its arrow) wait while a scan runs."""
        self.btn_rescan.setEnabled(not scanning)
        self.btn_rescan_menu.setEnabled(not scanning)

    def set_missing(self, n: int) -> None:
        self.btn_missing.setText(f"Missing ({n})")
        self.btn_missing.setVisible(n > 0)
