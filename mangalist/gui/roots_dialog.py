"""Roots manager: the list of library roots, each root's settings and exclusions, with a live preview of
what the exclusion patterns hide.

:class:`RootsEditor` is the content as a widget (the Settings dialog's Library section embeds it); :class:`RootsDialog`
wraps it with OK / Cancel. Edits are made on copies and written to the database only by :meth:`RootsEditor.commit`
(all validated first), so Cancel leaves everything as it was. Nothing on disk is changed by either.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..store import Root, RootError
from ..store.exclusions import InvalidPattern, TreeListing, list_tree, normalize_pattern, preview
from ..store.roots import validate_root
from ..store.schema import ENFORCE_NAMING_DEFAULT

BrowseFn = Callable[[QWidget, str, str], str]

ORIGIN_CHOICES = [("No hint", None), ("Manga", "manga"), ("Manhwa", "manhwa"), ("Webcomic", "webcomic")]
ENFORCE_CHOICES = [("Off", "off"), ("Ask (preview, then apply)", "ask"), ("Automatic", "automatic")]
PREVIEW_LIMIT = 500


def _default_browse(parent: QWidget, title: str, start: str) -> str:
    return QFileDialog.getExistingDirectory(parent, title, start or str(Path.home()))


class RootsEditor(QWidget):
    def __init__(self, db, parent: Optional[QWidget] = None, browse: Optional[BrowseFn] = None,
                 button_style: str = "", select: int = 0):
        super().__init__(parent)
        self.setObjectName("rootsEditor")
        self._db = db
        self._browse = browse or _default_browse
        self._button_style = button_style
        self._drafts: List[Root] = [copy.deepcopy(r) for r in db.list_roots()]
        self._removed: List[int] = []
        self._listings: Dict[str, TreeListing] = {}
        self._loading = False
        self.changed = False
        self._build_ui()
        self._refresh_list(select=select)

    # --- UI ------------------------------------------------------------------------------------------

    def _button(self, label: str) -> QPushButton:
        btn = QPushButton(label)
        if self._button_style:
            btn.setStyleSheet(self._button_style)
        return btn

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        intro = QLabel("A root is a folder whose subfolders are series folders. Exclusions are folders or "
                       "files (patterns relative to the root) that are never scanned.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        splitter = QSplitter(Qt.Horizontal)
        outer.addWidget(splitter, 1)

        # Left: the roots
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        self.root_list = QListWidget()
        self.root_list.currentRowChanged.connect(self._on_root_selected)
        lv.addWidget(self.root_list, 1)
        row = QHBoxLayout()
        self.btn_add = self._button("Add")
        self.btn_add.clicked.connect(self._on_add_clicked)
        self.btn_remove = self._button("Remove")
        self.btn_remove.setToolTip("Forget this root (nothing on disk is touched)")
        self.btn_remove.clicked.connect(self.remove_selected)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_remove)
        row.addStretch(1)
        lv.addLayout(row)
        splitter.addWidget(left)

        # Right: settings + exclusions
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        settings = QGroupBox("Root")
        form = QFormLayout(settings)
        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self._on_form_edited)
        form.addRow("Display name:", self.name_edit)
        self.path_edit = QLineEdit()
        self.path_edit.textEdited.connect(self._on_form_edited)
        self.path_edit.editingFinished.connect(self.update_preview)
        btn_path = self._button("Browse")
        btn_path.clicked.connect(self._on_browse_path)
        form.addRow("Folder:", self._with_button(self.path_edit, btn_path))
        self.origin_combo = QComboBox()
        for label, value in ORIGIN_CHOICES:
            self.origin_combo.addItem(label, value)
        self.origin_combo.setToolTip("Used only as evidence by the matcher")
        self.origin_combo.currentIndexChanged.connect(self._on_form_edited)
        form.addRow("Origin hint:", self.origin_combo)
        self.enforce_combo = QComboBox()
        for label, value in ENFORCE_CHOICES:
            self.enforce_combo.addItem(label, value)
        self.enforce_combo.setToolTip("What happens to names that do not follow the naming scheme")
        self.enforce_combo.currentIndexChanged.connect(self._on_form_edited)
        form.addRow("Enforce naming:", self.enforce_combo)
        self.staging_edit = QLineEdit()
        self.staging_edit.setPlaceholderText("None")
        self.staging_edit.setToolTip("Where arrivals wait before filing (on the same share as the root)")
        self.staging_edit.textEdited.connect(self._on_form_edited)
        btn_staging = self._button("Browse")
        btn_staging.clicked.connect(self._on_browse_staging)
        form.addRow("Staging folder:", self._with_button(self.staging_edit, btn_staging))
        rv.addWidget(settings)

        excl = QGroupBox("Exclusions")
        ev = QVBoxLayout(excl)
        hint = QLabel("<code>@Oneshots</code> any folder or file of that name &nbsp; <code>@Oneshots/**</code> "
                      "that folder in the root &nbsp; <code>*.txt</code> every .txt file &nbsp; "
                      "<code>Scans/</code> folders only")
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        ev.addWidget(hint)
        eh = QHBoxLayout()
        self.excl_list = QListWidget()
        self.excl_list.currentRowChanged.connect(lambda _r: self._update_buttons())
        eh.addWidget(self.excl_list, 1)
        pv = QVBoxLayout()
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        pv.addWidget(self.preview_label)
        self.preview_list = QListWidget()
        pv.addWidget(self.preview_list, 1)
        eh.addLayout(pv, 2)
        ev.addLayout(eh, 1)
        prow = QHBoxLayout()
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText("Pattern, e.g. @Oneshots/**")
        self.pattern_edit.textChanged.connect(self.update_preview)
        self.pattern_edit.returnPressed.connect(self.add_pattern)
        self.btn_add_pattern = self._button("Add")
        self.btn_add_pattern.clicked.connect(self.add_pattern)
        self.btn_remove_pattern = self._button("Remove")
        self.btn_remove_pattern.clicked.connect(self.remove_pattern)
        prow.addWidget(self.pattern_edit, 1)
        prow.addWidget(self.btn_add_pattern)
        prow.addWidget(self.btn_remove_pattern)
        ev.addLayout(prow)
        rv.addWidget(excl, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 720])

        self.error_label = QLabel("")
        self.error_label.setProperty("tone", "bad")
        self.error_label.setWordWrap(True)
        outer.addWidget(self.error_label)

    @staticmethod
    def _with_button(edit: QLineEdit, button: QPushButton) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit, 1)
        h.addWidget(button)
        return w

    # --- state ---------------------------------------------------------------------------------------

    @property
    def drafts(self) -> List[Root]:
        return self._drafts

    def current(self) -> Optional[Root]:
        i = self.root_list.currentRow()
        return self._drafts[i] if 0 <= i < len(self._drafts) else None

    def _refresh_list(self, select: Optional[int] = None) -> None:
        self._loading = True
        self.root_list.clear()
        for r in self._drafts:
            item = QListWidgetItem(f"{r.name}\n{r.path}")
            item.setToolTip(r.path)
            if not Path(r.path).is_dir():
                item.setForeground(QColor("#b71c1c"))
                item.setToolTip(f"{r.path}\n(not reachable now)")
            self.root_list.addItem(item)
        self._loading = False
        if select is not None and self._drafts:
            self.root_list.setCurrentRow(max(0, min(select, len(self._drafts) - 1)))
        self._on_root_selected(self.root_list.currentRow())

    def _on_root_selected(self, _row: int) -> None:
        if self._loading:
            return
        r = self.current()
        self._loading = True
        enabled = r is not None
        for w in (self.name_edit, self.path_edit, self.origin_combo, self.enforce_combo, self.staging_edit,
                  self.excl_list, self.pattern_edit):
            w.setEnabled(enabled)
        self.name_edit.setText(r.name if r else "")
        self.path_edit.setText(r.path if r else "")
        self.origin_combo.setCurrentIndex(max(0, self.origin_combo.findData(r.origin_hint if r else None)))
        self.enforce_combo.setCurrentIndex(
            max(0, self.enforce_combo.findData(r.enforce_naming if r else ENFORCE_NAMING_DEFAULT)))
        self.staging_edit.setText((r.staging_folder or "") if r else "")
        self.excl_list.clear()
        for pat in (r.exclusions if r else []):
            self.excl_list.addItem(pat)
        self._loading = False
        self.pattern_edit.clear()
        self.update_preview()

    def _on_form_edited(self, *_args) -> None:
        if self._loading:
            return
        r = self.current()
        if r is None:
            return
        r.name = self.name_edit.text()
        r.path = self.path_edit.text().strip()
        r.origin_hint = self.origin_combo.currentData()
        r.enforce_naming = self.enforce_combo.currentData()
        r.staging_folder = self.staging_edit.text().strip() or None
        item = self.root_list.currentItem()
        if item is not None:
            item.setText(f"{r.name or Path(r.path).name}\n{r.path}")
        self.changed = True

    def _update_buttons(self) -> None:
        has_root = self.current() is not None
        self.btn_remove.setEnabled(has_root)
        self.btn_add_pattern.setEnabled(has_root and self._typed_pattern() is not None)
        self.btn_remove_pattern.setEnabled(has_root and self.excl_list.currentRow() >= 0)

    # --- roots ---------------------------------------------------------------------------------------

    def _on_add_clicked(self) -> None:
        start = self.current().path if self.current() else ""
        path = self._browse(self, "Add a root (a folder of series folders)", start)
        if path:
            self.add_root(path)

    def add_root(self, path: str) -> Optional[Root]:
        draft = Root(id=None, name="", path=path)
        try:
            validate_root(draft, self._drafts)
        except RootError as exc:
            self.error_label.setText(str(exc))
            return None
        self.error_label.clear()
        self._drafts.append(draft)
        self.changed = True
        self._refresh_list(select=len(self._drafts) - 1)
        return draft

    def remove_selected(self) -> None:
        i = self.root_list.currentRow()
        if not (0 <= i < len(self._drafts)):
            return
        r = self._drafts.pop(i)
        if r.id is not None:
            self._removed.append(r.id)
        self.changed = True
        self._refresh_list(select=i)

    def _on_browse_path(self) -> None:
        r = self.current()
        if r is None:
            return
        path = self._browse(self, "Root folder", r.path)
        if path:
            self.path_edit.setText(path)
            self._on_form_edited()
            self.update_preview()

    def _on_browse_staging(self) -> None:
        r = self.current()
        if r is None:
            return
        path = self._browse(self, "Staging folder", r.staging_folder or r.path)
        if path:
            self.staging_edit.setText(path)
            self._on_form_edited()

    # --- exclusions ----------------------------------------------------------------------------------

    def _typed_pattern(self) -> Optional[str]:
        text = self.pattern_edit.text()
        if not text.strip():
            return None
        try:
            return normalize_pattern(text)
        except InvalidPattern:
            return None

    def add_pattern(self) -> None:
        r = self.current()
        pat = self._typed_pattern()
        if r is None or pat is None:
            return
        if pat not in r.exclusions:
            r.exclusions.append(pat)
            self.excl_list.addItem(pat)
            self.changed = True
        self.pattern_edit.clear()
        self.update_preview()

    def remove_pattern(self) -> None:
        r = self.current()
        i = self.excl_list.currentRow()
        if r is None or not (0 <= i < len(r.exclusions)):
            return
        r.exclusions.pop(i)
        self.excl_list.takeItem(i)
        self.changed = True
        self.update_preview()

    def _listing(self, path: str) -> TreeListing:
        if path not in self._listings:
            self._listings[path] = list_tree(Path(path))
        return self._listings[path]

    def update_preview(self) -> None:
        """Show what the root's patterns, plus the one being typed, hide."""
        self._update_buttons()
        self.preview_list.clear()
        r = self.current()
        if r is None:
            self.preview_label.setText("")
            return
        patterns = list(r.exclusions)
        typed = self.pattern_edit.text()
        typed_note = ""
        if typed.strip():
            try:
                patterns.append(normalize_pattern(typed))
            except InvalidPattern as exc:
                typed_note = f" Pattern not valid: {exc}."
        if not r.path or not Path(r.path).is_dir():
            self.preview_label.setText("The folder is not reachable now; no preview." + typed_note)
            return
        listing = self._listing(r.path)
        items = preview(listing, patterns)
        for it in items[:PREVIEW_LIMIT]:
            label = it.rel_path + ("/" if it.is_dir else "")
            if it.hidden_below:
                label += f"   (+{it.hidden_below} inside)"
            row = QListWidgetItem(label)
            row.setToolTip(f"hidden by {it.pattern}")
            self.preview_list.addItem(row)
        if not patterns:
            text = "No exclusions: everything in the root is scanned."
        elif not items:
            text = "Excluded: nothing in this root matches."
        else:
            total = len(items) + sum(i.hidden_below for i in items)
            text = f"Excluded: {len(items)} item(s) ({total} with their contents)"
            if len(items) > PREVIEW_LIMIT:
                text += f", first {PREVIEW_LIMIT} shown"
            text += "."
        if listing.truncated:
            text += " (Preview of the first part of the root only.)"
        self.preview_label.setText(text + typed_note)

    # --- save ----------------------------------------------------------------------------------------

    def commit(self) -> bool:
        """Validate every draft and write them (and the removals) to the database; False (with the reason in
        ``error_label``) when something is wrong - nothing is written then."""
        try:
            for i, r in enumerate(self._drafts):
                r.position = i
                validate_root(r, self._drafts)
        except RootError as exc:
            self.error_label.setText(str(exc))
            return False
        try:
            for root_id in self._removed:
                self._db.remove_root(root_id)
            for r in self._drafts:
                if r.id is None:
                    saved = self._db.add_root(r.path, r.name, origin_hint=r.origin_hint,
                                              enforce_naming=r.enforce_naming, staging_folder=r.staging_folder,
                                              exclusions=list(r.exclusions))
                    r.id = saved.id
                else:
                    self._db.update_root(r)
        except RootError as exc:
            self.error_label.setText(str(exc))
            return False
        self._removed.clear()
        self.changed = True
        self.error_label.clear()
        return True


class RootsDialog(QDialog):
    def __init__(self, db, parent: Optional[QWidget] = None, browse: Optional[BrowseFn] = None,
                 button_style: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Roots")
        self.resize(980, 600)
        self.editor = RootsEditor(db, self, browse=browse, button_style=button_style)
        outer = QVBoxLayout(self)
        outer.addWidget(self.editor, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    @property
    def changed(self) -> bool:
        return self.editor.changed

    def accept(self) -> None:
        if self.editor.commit():
            super().accept()
