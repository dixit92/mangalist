"""Settings > Connected services > Built-in extensions (prototype/mihon-mangalist): MangaList's own extension host.

- :class:`ExtensionHostPanel` - the host's form: its address (in the container the host runs next to MangaList and starts
  by itself while "Built-in extensions" is chosen; on the desktop, start it yourself and enter its address), the folder
  MangaList files finished chapters from (empty: the host's own), the extension repository, FlareSolverr and the WebView
  switch; Test connection (off the UI thread, the values as typed) and Save.
- :class:`ExtensionsPanel` - the Extensions page: the repository's extensions with what is installed (English and
  multi-language ones, and every installed one, unless "Show all languages"), a filter, and **buttons** for the selected
  row - Install, Update, Uninstall, Source settings (owner: buttons, not a right-click menu only: an iPad has none).
- :class:`SourcePreferencesPanel` - a source's settings as its extension declares them: a list (a drop-down), a
  checkbox / switch, a text (with Apply) and a multi-select list (checkboxes); every change is sent to the host at once.

Every call that asks the host runs off the UI thread (:func:`~mangalist.gui.background.start_call`); a failure shows the
host's own message.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..services.extensions.client import (
    PREF_CHECKBOX,
    PREF_EDITTEXT,
    PREF_LIST,
    PREF_MULTISELECT,
    PREF_SWITCH,
    SourcePreference,
)
from ..store.downloads import DEFAULT_EXTENSION_REPO, ExtensionHostConfig
from .background import BackgroundCall, start_call
from .download_widgets import button, checkbox, flat_table, hbox, label
from .tables import cell, resizable_columns

HOST_INTRO = ("MangaList's own extension host runs Mihon / Tachiyomi extensions (MangaDex, Weeb Central, MangaFire, ...) "
              "without Suwayomi and downloads chapters for MangaList. In the container it starts by itself while "
              "\"Built-in extensions\" is chosen above; on a desktop, start the host yourself and enter its address.")
HOST_FOLDER_HELP = ("Where MangaList finds the host's downloads (the folder holding \"mangas\"), as MangaList sees it. "
                    "Empty: the host's own folder - right whenever the host runs next to MangaList, as in the container.")
HOST_FLARESOLVERR_HELP = ("Sources behind Cloudflare need FlareSolverr: its address, e.g. http://<unraid-ip>:8191 (the "
                          "box's address, not localhost). Empty: MANGALIST_FLARESOLVERR_URL, else none.")
HOST_WEBVIEW_HELP = ("Some sources (Comix, ...) need a browser engine: the host downloads a Chromium bundle into its data "
                     "folder the first time one is used.")
LIST_LANGS = ("en", "all", "")          # shown without "Show all languages" (with every installed extension)
COLUMNS = ("Extension", "Language", "Version", "Status")


def _status(row) -> str:
    if row.has_update:
        return f"Update: {row.installed_version} → {row.repo_version}"
    if row.installed:
        return "Installed" if row.repo_version else "Installed (no longer in the repository)"
    return ""


class _Calls:
    """The panel's background calls, abandoned when it closes."""

    def __init__(self) -> None:
        self.calls: List[BackgroundCall] = []

    def start(self, fn, on_done, on_error, on_finished: Optional[Callable[[], None]] = None) -> BackgroundCall:
        made: list = []

        def finished() -> None:
            if made and made[0] in self.calls:
                self.calls.remove(made[0])
            if on_finished is not None:
                on_finished()

        call = start_call(fn, on_done, on_error, finished)
        made.append(call)
        self.calls.append(call)
        return call

    def busy(self) -> bool:
        return bool(self.calls)

    def stop(self) -> None:
        for call in list(self.calls):
            call.abandon()
        self.calls.clear()


def _say(target, text: str, tone: str = "") -> None:
    from .download_style import set_tone

    set_tone(target, tone)
    target.setText(text)


# --- the host's form ----------------------------------------------------------------------------------------------

class ExtensionHostPanel(QWidget):
    saved_changes = Signal()

    def __init__(self, backend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("extensionHostPanel")
        self._backend = backend
        self._calls = _Calls()
        self.saved = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(label(HOST_INTRO, wrap=True))
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://127.0.0.1:4570")
        form.addRow("Address:", self.url_edit)
        self.folder_edit = QLineEdit()
        folder = QWidget()
        fl = QVBoxLayout(folder)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(2)
        fl.addWidget(self.folder_edit)
        fl.addWidget(label(HOST_FOLDER_HELP, "muted", wrap=True))
        form.addRow("Download folder:", folder)
        self.repo_edit = QLineEdit()
        self.repo_edit.setPlaceholderText(DEFAULT_EXTENSION_REPO)
        form.addRow("Extension repository:", self.repo_edit)
        self.flare_edit = QLineEdit()
        self.flare_edit.setPlaceholderText("http://192.168.1.10:8191")
        flare = QWidget()
        frl = QVBoxLayout(flare)
        frl.setContentsMargins(0, 0, 0, 0)
        frl.setSpacing(2)
        frl.addWidget(self.flare_edit)
        frl.addWidget(label(HOST_FLARESOLVERR_HELP, "muted", wrap=True))
        form.addRow("FlareSolverr:", flare)
        self.webview_check = checkbox("WebView sources (Chromium)", True, tip=HOST_WEBVIEW_HELP)
        form.addRow("", self.webview_check)
        outer.addLayout(form)
        self.btn_test = button("Test connection")
        self.btn_test.clicked.connect(self.test_connection)
        outer.addLayout(hbox(self.btn_test, None))
        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        outer.addStretch(1)
        self._config = ExtensionHostConfig()
        self._load()

    def _load(self) -> None:
        self._config = c = self._backend.load_extension_host()
        self.url_edit.setText(c.url)
        self.folder_edit.setText(c.download_dir)
        self.folder_edit.setPlaceholderText(c.downloads_path or "the host's own downloads folder")
        self.repo_edit.setText(c.repo_url)
        self.flare_edit.setText(c.flaresolverr_url)
        self.webview_check.setChecked(c.webview)

    def typed(self) -> ExtensionHostConfig:
        return ExtensionHostConfig(url=self.url_edit.text().strip(), download_dir=self.folder_edit.text().strip(),
                                   downloads_path=self._config.downloads_path, repo_url=self.repo_edit.text().strip(),
                                   flaresolverr_url=self.flare_edit.text().strip(),
                                   webview=self.webview_check.isChecked())

    def save(self) -> bool:
        from .downloads_backend import BackendError

        try:
            self._backend.save_extension_host(self.typed())
        except BackendError as exc:
            _say(self.status_label, f"Could not save: {exc}", "bad")
            return False
        self.saved = True
        self._load()
        _say(self.status_label, "Saved.", "ok")
        self.saved_changes.emit()
        return True

    def test_connection(self) -> bool:
        if self._calls.busy():
            return False
        config = self.typed()
        backend = self._backend
        self.btn_test.setEnabled(False)
        _say(self.status_label, "Testing the connection...")
        self._calls.start(lambda: backend.test_extension_host(config), self._tested,
                          lambda message: _say(self.status_label, f"Connection failed: {message}", "bad"),
                          lambda: self.btn_test.setEnabled(True))
        return True

    def _tested(self, check) -> None:
        notes = check.notes()
        _say(self.status_label, f"Connected: extension host {check.version}, {check.extensions} extension(s), "
                                f"{check.sources} source(s); downloads in {check.files_from or '?'}."
             + (" " + " ".join(notes) if notes else ""), "warn" if notes else "ok")
        if check.downloads_path:
            self.folder_edit.setPlaceholderText(check.downloads_path)

    def stop(self) -> None:
        self._calls.stop()


# --- the Extensions page ------------------------------------------------------------------------------------------

class ExtensionsPanel(QWidget):
    """The repository's extensions, what is installed, and buttons for the selected one."""

    changed = Signal()                  # an extension was installed / updated / removed (the sources changed)

    def __init__(self, backend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("extensionsPanel")
        self._backend = backend
        self._calls = _Calls()
        self._rows: list = []
        self.prefs_panel: Optional[SourcePreferencesPanel] = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        config = backend.load_extension_host()
        self.repo_label = label(f"Repository: {config.repo_url or 'the default (keiyoushi)'}", "muted", wrap=True,
                                selectable=True)
        outer.addWidget(self.repo_label)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Filter by name")
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(lambda _t: self._render())
        self.installed_check = checkbox("Installed only")
        self.installed_check.toggled.connect(lambda _on: self._render())
        self.all_langs_check = checkbox("Show all languages")
        self.all_langs_check.toggled.connect(lambda _on: self._render())
        self.btn_reload = button("Reload")
        self.btn_reload.clicked.connect(self.reload)
        outer.addLayout(hbox(self.filter_edit, self.installed_check, self.all_langs_check, self.btn_reload))
        self.table = flat_table("extensionsTable", COLUMNS)
        resizable_columns(self.table, {0: 260, 1: 90, 2: 150})
        self.table.itemSelectionChanged.connect(self._selection_changed)
        outer.addWidget(self.table, 1)
        self.btn_install = button("Install", primary=True)
        self.btn_install.clicked.connect(lambda: self._act("install"))
        self.btn_update = button("Update")
        self.btn_update.clicked.connect(lambda: self._act("update"))
        self.btn_uninstall = button("Uninstall")
        self.btn_uninstall.clicked.connect(lambda: self._act("uninstall"))
        self.btn_prefs = button("Source settings")
        self.btn_prefs.clicked.connect(self.open_preferences)
        self.btn_update_all = button("Update all")
        self.btn_update_all.clicked.connect(self.update_all)
        outer.addLayout(hbox(self.btn_install, self.btn_update, self.btn_uninstall, self.btn_prefs, None,
                             self.btn_update_all))
        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        self.prefs_box = QVBoxLayout()
        outer.addLayout(self.prefs_box)
        self._selection_changed()
        self.reload()

    # --- the list ---

    def reload(self) -> bool:
        if self._calls.busy():
            return False
        _say(self.status_label, "Reading the repository and the installed extensions...")
        self._set_busy(True)
        self._calls.start(self._backend.extension_repo, self.show_rows,
                          lambda message: _say(self.status_label, f"Could not read them: {message}", "bad"),
                          lambda: self._set_busy(False))
        return True

    def show_rows(self, rows) -> None:
        self._rows = list(rows)
        installed = sum(1 for r in self._rows if r.installed)
        updates = sum(1 for r in self._rows if r.has_update)
        _say(self.status_label, f"{installed} installed" + (f", {updates} with an update" if updates else "")
             + f" · {len(self._rows)} in the repository")
        self._render()

    def visible_rows(self) -> list:
        text = self.filter_edit.text().strip().casefold()
        out = []
        for r in self._rows:
            if self.installed_check.isChecked() and not r.installed:
                continue
            if not self.all_langs_check.isChecked() and not r.installed and r.lang not in LIST_LANGS:
                continue
            if text and text not in r.name.casefold() and text not in r.pkg.casefold():
                continue
            out.append(r)
        return out

    def _render(self) -> None:
        selected = self.selected()
        rows = self.visible_rows()
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            name = cell(r.name + ("  (18+)" if r.nsfw else ""))
            name.setData(Qt.ItemDataRole.UserRole, r.pkg)
            name.setToolTip(f"{r.pkg}\n" + "\n".join(f"{n} ({lang}) · {sid}" for sid, n, lang in r.sources))
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, cell(r.lang or "-"))
            self.table.setItem(i, 2, cell(r.installed_version or r.repo_version))
            self.table.setItem(i, 3, cell(_status(r)))
            if selected is not None and r.pkg == selected.pkg:
                self.table.selectRow(i)
        self.btn_update_all.setEnabled(any(r.has_update for r in self._rows) and not self._calls.busy())
        self._selection_changed()

    def selected(self):
        items = self.table.selectedItems()
        if not items:
            return None
        pkg = self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        return next((r for r in self._rows if r.pkg == pkg), None)

    def select(self, pkg: str) -> bool:
        for i in range(self.table.rowCount()):
            if self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) == pkg:
                self.table.selectRow(i)
                return True
        return False

    def _selection_changed(self) -> None:
        row = self.selected()
        idle = not self._calls.busy()
        self.btn_install.setEnabled(idle and row is not None and not row.installed and bool(row.repo_version))
        self.btn_update.setEnabled(idle and row is not None and row.has_update)
        self.btn_uninstall.setEnabled(idle and row is not None and row.installed)
        self.btn_prefs.setEnabled(idle and row is not None and row.installed and bool(row.sources))

    def _set_busy(self, busy: bool) -> None:
        self.btn_reload.setEnabled(not busy)
        if busy:
            for b in (self.btn_install, self.btn_update, self.btn_uninstall, self.btn_prefs, self.btn_update_all):
                b.setEnabled(False)
        else:
            self._selection_changed()
            self.btn_update_all.setEnabled(any(r.has_update for r in self._rows))

    # --- the buttons ---

    def _act(self, what: str) -> bool:
        row = self.selected()
        if row is None or self._calls.busy():
            return False
        backend = self._backend
        fn = {"install": backend.install_extension, "update": backend.update_extension,
              "uninstall": backend.uninstall_extension}[what]
        doing = {"install": "Installing", "update": "Updating", "uninstall": "Removing"}[what]
        done = {"install": "Installed", "update": "Updated", "uninstall": "Removed"}[what]
        _say(self.status_label, f"{doing} {row.name}...")
        self._set_busy(True)
        pkg, name = row.pkg, row.name

        def ok(_result) -> None:
            self.changed.emit()
            self._after = f"{done} {name}."
            self._calls.start(backend.extension_repo, self._reloaded,
                              lambda message: _say(self.status_label, f"{done} {name}; the list could not be read "
                                                                      f"again: {message}", "warn"),
                              lambda: self._set_busy(False))

        self._calls.start(lambda: fn(pkg), ok,
                          lambda message: _say(self.status_label, f"{name}: {message}", "bad"),
                          lambda: self._set_busy(self._calls.busy()))
        return True

    def _reloaded(self, rows) -> None:
        self.show_rows(rows)
        _say(self.status_label, getattr(self, "_after", "Done."), "ok")

    def update_all(self) -> bool:
        pending = [r for r in self._rows if r.has_update]
        if not pending or self._calls.busy():
            return False
        backend = self._backend
        _say(self.status_label, f"Updating {len(pending)} extension(s)...")
        self._set_busy(True)

        def run():
            failed = []
            for r in pending:
                try:
                    backend.update_extension(r.pkg)
                except Exception as exc:  # noqa: BLE001 - the others still update; said below
                    failed.append(f"{r.name}: {exc}")
            return failed, backend.extension_repo()

        def done(result) -> None:
            failed, rows = result
            self.changed.emit()
            self.show_rows(rows)
            if failed:
                _say(self.status_label, "Not updated: " + "; ".join(failed), "bad")
            else:
                _say(self.status_label, f"Updated {len(pending)} extension(s).", "ok")

        self._calls.start(run, done, lambda message: _say(self.status_label, message, "bad"),
                          lambda: self._set_busy(self._calls.busy()))
        return True

    def open_preferences(self) -> Optional["SourcePreferencesPanel"]:
        row = self.selected()
        if row is None or not row.installed or not row.sources:
            return None
        self.close_preferences()
        self.prefs_panel = SourcePreferencesPanel(self._backend, row.name, row.sources, self)
        self.prefs_panel.closed.connect(self.close_preferences)
        self.prefs_box.addWidget(self.prefs_panel)
        return self.prefs_panel

    def close_preferences(self) -> None:
        if self.prefs_panel is not None:
            self.prefs_panel.stop()
            self.prefs_panel.setParent(None)
            self.prefs_panel.deleteLater()
            self.prefs_panel = None

    def stop(self) -> None:
        self.close_preferences()
        self._calls.stop()


# --- a source's settings ------------------------------------------------------------------------------------------

class SourcePreferencesPanel(QWidget):
    """One source's settings (pick the source when the extension has several)."""

    closed = Signal()

    def __init__(self, backend, name: str, sources, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("sourcePreferencesPanel")
        self._backend = backend
        self._calls = _Calls()
        self.prefs: List[SourcePreference] = []
        self.editors: Dict[str, QWidget] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        self.source_combo = QComboBox()
        for sid, sname, lang in sources:
            self.source_combo.addItem(f"{sname} ({lang})", sid)
        english = next((i for i, (_s, _n, lang) in enumerate(sources) if lang == "en"), 0)
        self.source_combo.setCurrentIndex(english)
        self.source_combo.currentIndexChanged.connect(lambda _i: self.load())
        self.btn_close = button("Close")
        self.btn_close.clicked.connect(self.closed.emit)
        outer.addLayout(hbox(label(f"{name} settings", "h3"), self.source_combo, None, self.btn_close))
        self.form = QFormLayout()
        outer.addLayout(self.form)
        self.status_label = label("", wrap=True)
        outer.addWidget(self.status_label)
        self.load()

    @property
    def source_id(self) -> str:
        return str(self.source_combo.currentData())

    def load(self) -> bool:
        sid = self.source_id
        _say(self.status_label, "Reading the source's settings...")
        self._calls.start(lambda: self._backend.source_preferences(sid), self.show_preferences,
                          lambda message: _say(self.status_label, f"Could not read them: {message}", "bad"))
        return True

    def show_preferences(self, prefs) -> None:
        self.prefs = [p for p in prefs if p.visible]
        while self.form.rowCount():
            self.form.removeRow(0)
        self.editors.clear()
        for pref in self.prefs:
            editor = self._editor(pref)
            editor.setEnabled(pref.enabled and bool(pref.kind))
            if pref.summary and "%s" not in pref.summary:
                editor.setToolTip(pref.summary)
            self.editors[pref.key] = editor
            self.form.addRow(pref.title + ":" if pref.kind not in (PREF_CHECKBOX, PREF_SWITCH) else "", editor)
        _say(self.status_label, "" if self.prefs else "This source has no settings.")

    def _editor(self, pref: SourcePreference) -> QWidget:
        if pref.kind == PREF_LIST:
            combo = QComboBox()
            for text, value in zip(pref.entries or pref.entry_values, pref.entry_values):
                combo.addItem(text, value)
            index = combo.findData(pref.value)
            combo.setCurrentIndex(max(0, index))
            combo.activated.connect(lambda _i, p=pref, c=combo: self.change(p, c.currentData()))
            return combo
        if pref.kind in (PREF_CHECKBOX, PREF_SWITCH):
            box = QCheckBox(pref.title)
            box.setChecked(bool(pref.value))
            box.toggled.connect(lambda on, p=pref: self.change(p, on))
            return box
        if pref.kind == PREF_MULTISELECT:
            holder = QWidget()
            lay = QVBoxLayout(holder)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            chosen = set(pref.value or ())
            boxes = []
            for text, value in zip(pref.entries or pref.entry_values, pref.entry_values):
                box = QCheckBox(text)
                box.setProperty("value", value)
                box.setChecked(value in chosen)
                boxes.append(box)
                lay.addWidget(box)
            for box in boxes:
                box.toggled.connect(lambda _on, p=pref, bs=boxes: self.change(
                    p, [b.property("value") for b in bs if b.isChecked()]))
            holder.boxes = boxes                                  # type: ignore[attr-defined]
            return holder
        # edit text (and a kind MangaList cannot edit: shown as text, read-only)
        holder = QWidget()
        edit = QLineEdit("" if pref.value is None else str(pref.value))
        apply = button("Apply")
        apply.clicked.connect(lambda _c=False, p=pref, e=edit: self.change(p, e.text()))
        edit.returnPressed.connect(apply.click)
        lay = hbox(edit, apply, spacing=6)
        holder.setLayout(lay)
        holder.edit = edit                                        # type: ignore[attr-defined]
        holder.apply = apply                                      # type: ignore[attr-defined]
        return holder

    def change(self, pref: SourcePreference, value) -> bool:
        sid = self.source_id
        _say(self.status_label, f"Saving {pref.title}...")

        def done(prefs) -> None:
            self.prefs = [p for p in prefs if p.visible]
            _say(self.status_label, f"{pref.title}: saved.", "ok")

        self._calls.start(lambda: self._backend.set_source_preference(sid, pref, value), done,
                          lambda message: _say(self.status_label, f"{pref.title}: not saved: {message}", "bad"))
        return True

    def busy(self) -> bool:
        return self._calls.busy()

    def stop(self) -> None:
        self._calls.stop()
