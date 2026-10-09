"""Settings > Library: the roots MangaList manages (name, folder, how many series, the MangaPixer library each maps to),
Add a root, Edit (the roots editor with its exclusions and live preview), and the file-naming placeholder (phase 2).

Editing is on copies, written only by Save in the editor (Cancel leaves everything as it was). Nothing on disk is changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtWidgets import QFileDialog, QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .download_widgets import button, card, hbox, label
from .roots_dialog import BrowseFn, RootsEditor
from .settings_common import SectionPage, back_link, placeholder

LEAD = "The folders MangaList manages. MangaList files downloads only into these."
NAMING_TEXT = "File naming - one naming scheme per root, applied by the renamer (phase 2)."
NO_ROOTS = "No roots yet. Add the folder that holds your series folders."


def _default_browse(parent: QWidget, title: str, start: str) -> str:
    return QFileDialog.getExistingDirectory(parent, title, start or str(Path.home()))


class LibraryPage(SectionPage):
    def __init__(self, db, parent: Optional[QWidget] = None, browse: Optional[BrowseFn] = None, cache=None):
        super().__init__("Library", LEAD, parent)
        self._db = db
        self._browse = browse or _default_browse
        self._cache = cache
        self.editor: Optional[RootsEditor] = None
        self.stack = QStackedWidget()
        self.body.addWidget(self.stack)

        overview = QWidget()
        ov = QVBoxLayout(overview)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(14)
        self.roots_box = card("true")
        self.roots_layout = QVBoxLayout(self.roots_box)
        self.roots_layout.setContentsMargins(0, 0, 0, 0)
        self.roots_layout.setSpacing(0)
        ov.addWidget(self.roots_box)
        self.btn_add = button("Add a root")
        self.btn_add.clicked.connect(self.add_root)
        ov.addLayout(hbox(self.btn_add, None))
        ov.addWidget(placeholder(NAMING_TEXT))
        ov.addStretch(1)
        self.stack.addWidget(overview)

        self.editor_page = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_page)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(10)
        self.stack.addWidget(self.editor_page)
        self.refresh()

    # --- the overview ------------------------------------------------------------------------------------

    def refresh(self) -> None:
        while self.roots_layout.count():
            item = self.roots_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        roots = self._db.list_roots()
        if not roots:
            empty = label(NO_ROOTS, "lead", wrap=True)
            empty.setContentsMargins(16, 12, 16, 12)
            self.roots_layout.addWidget(empty)
        for index, root in enumerate(roots):
            self.roots_layout.addWidget(self._row(index, root))
        self.roots = roots

    def _row(self, index: int, root) -> QFrame:
        row = QFrame()
        row.setObjectName("rootRow")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(14)
        name = label(root.name or Path(root.path).name, "name")
        path = label(root.path, "mono", selectable=True)
        series = label(self._summary(root), "muted")
        edit = button("Edit")
        edit.setProperty("rootIndex", index)
        edit.clicked.connect(lambda _=False, i=index: self.edit_root(i))
        lay.addWidget(name)
        lay.addWidget(path, 1)
        lay.addWidget(series)
        lay.addWidget(edit)
        row.edit_button = edit                                  # type: ignore[attr-defined]
        return row

    def _summary(self, root) -> str:
        try:
            count = len(self._db.list_series(root.id))
        except Exception:  # noqa: BLE001 - a summary only
            count = 0
        text = f"{count} series"
        library = self._mangapixer_library(root)
        return f"{text} · MangaPixer: {library}" if library else text

    def _mangapixer_library(self, root) -> str:
        if self._cache is None:
            return ""
        try:
            mapping = self._cache.mapping(root.id)
            if mapping is None or not mapping.library_id:
                return ""
            lib = self._cache.library(mapping.library_id)
            return lib.display_name if lib is not None else ""
        except Exception:  # noqa: BLE001
            return ""

    # --- the editor -----------------------------------------------------------------------------------------

    def edit_root(self, index: int = 0, add_path: Optional[str] = None) -> RootsEditor:
        """Open the roots editor on root *index* (optionally with a new root at *add_path* added to it)."""
        self._close_editor()
        editor = RootsEditor(self._db, self.editor_page, browse=self._browse, select=index)
        if add_path:
            editor.add_root(add_path)
        self.editor = editor
        back = back_link("Library")
        back.clicked.connect(self.cancel_edit)
        save = button("Save", primary=True)
        save.clicked.connect(self.save_edit)
        cancel = button("Cancel")
        cancel.clicked.connect(self.cancel_edit)
        self._edit_buttons = (back, save, cancel)
        self.editor_layout.addLayout(hbox(back, None))
        self.editor_layout.addWidget(editor, 1)
        self.editor_layout.addLayout(hbox(None, cancel, save))
        self.stack.setCurrentWidget(self.editor_page)
        return editor

    def add_root(self) -> Optional[RootsEditor]:
        start = self.roots[-1].path if getattr(self, "roots", None) else ""
        path = self._browse(self, "Add a root (a folder of series folders)", start)
        if not path:
            return None
        return self.edit_root(len(self.roots), add_path=path)

    def save_edit(self) -> bool:
        if self.editor is None or not self.editor.commit():
            return False
        self._close_editor()
        self.refresh()
        self.roots_changed.emit()
        return True

    def cancel_edit(self) -> None:
        self._close_editor()

    def _close_editor(self) -> None:
        if self.editor is not None:
            self.editor.setParent(None)
            self.editor.deleteLater()
            self.editor = None
        while self.editor_layout.count():
            item = self.editor_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                lay = item.layout()
                while lay.count():
                    child = lay.takeAt(0)
                    if child.widget() is not None:
                        child.widget().deleteLater()
        self.stack.setCurrentIndex(0)

    def stop(self) -> None:
        self._close_editor()
