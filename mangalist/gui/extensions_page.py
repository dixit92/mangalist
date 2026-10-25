"""Settings > Connected services > Built-in extensions (prototype/mihon-mangalist): MangaList's own extension host.

- :class:`ExtensionHostPanel` - the host's form: its address (in the container the host runs next to MangaList and starts
  by itself while "Built-in extensions" is chosen; on the desktop, start it yourself and enter its address), the folder
  MangaList files finished chapters from (empty: the host's own), FlareSolverr, the WebView switch and Auto-update (off by
  default: the scheduled update check installs the updates it finds); Test connection (off the UI thread, the values as
  typed) and Save. Above them, :class:`RepositoriesEditor` - the host's extension repositories (Suwayomi's
  "extensionRepos"), changed at once: **Add** (by the index's URL; the host fetches and checks it before it is listed)
  and **Remove** (extensions installed from it stay installed and get no updates) - buttons, for the iPad.
- :class:`ExtensionsPanel` - the Extensions page: every repository's extensions with what is installed (English and
  multi-language ones, and every installed one, unless "Show all languages"), a Repository column and filter (the same
  extension in two repositories is two rows; one installed from one of them updates only from that one), a name filter,
  and **buttons** for the selected row - Install, Update, Uninstall, Source settings (owner: buttons, not a right-click
  menu only: an iPad has none).
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
    QListWidget,
    QListWidgetItem,
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
              "\"Built-in extensions\" is chosen above - and, with Suwayomi chosen, whenever this page, Test "
              "connection or a carry-over needs it (it stops again 30 minutes later); on a desktop, start the host "
              "yourself and enter its address.")
HOST_FOLDER_HELP = ("Where MangaList finds the host's downloads (the folder holding \"mangas\"), as MangaList sees it. "
                    "Empty: the host's own folder - right whenever the host runs next to MangaList, as in the container.")
HOST_DOWNLOADS_HELP = ("Where the host downloads chapters (the container starts it with this folder). Put it on the same disk "
                       "as the library: finished chapters are then hard-linked into it instead of copied. Empty: the "
                       "container's MANGALIST_EXTENSION_DOWNLOADS, else the host's data folder. The host restarts to use "
                       "a changed folder (once the chapters waiting in the old one are filed); the folder, or its "
                       "parent, must exist.")
HOST_FLARESOLVERR_HELP = ("Sources behind Cloudflare need FlareSolverr: its address, e.g. http://<unraid-ip>:8191 (the "
                          "box's address, not localhost). Empty: MANGALIST_FLARESOLVERR_URL, else none.")
HOST_WEBVIEW_HELP = ("Some sources (Comix, ...) need a browser engine: the host downloads a Chromium bundle into its data "
                     "folder the first time one is used.")
HOST_BROWSER_HELP = ("Delete the Chromium bundle and its cache from the host's data folder (they are not used while the "
                     "WebView is off). Turning the WebView on again downloads them when a source needs them.")
HOST_AUTO_UPDATE_HELP = ("When on, the scheduled update check (Settings > Scheduled tasks, daily by default) installs the "
                         "updates it finds, each checked against its repository's signing key as for Update; one that "
                         "fails keeps the old version and is reported. An extension is never updated while one of its "
                         "chapters downloads: it waits for the next check.")
REPOS_HELP = ("Mihon extension repositories (an index.json address, e.g. keiyoushi's). The host reads each one's index "
              "and installs an extension only when it is signed with that repository's own key. Removing a repository "
              "keeps the extensions installed from it; they just get no more updates. Changes apply at once.")
LIST_LANGS = ("en", "all", "")          # shown without "Show all languages" (with every installed extension)
COLUMNS = ("Extension", "Language", "Repository", "Version", "Status")
ALL_REPOS = ""                          # the repository filter's "All repositories"


def _status(row) -> str:
    if row.has_update:
        return f"Update: {row.installed_version} → {row.repo_version}"
    if row.installed:
        if not row.repo_listed:
            return "Installed (its repository was removed: no updates)"
        return "Installed" if row.repo_version else "Installed (no longer in the repository)"
    if row.installed_from:
        return f"Installed from {row.installed_from}"
    return ""


def _short_key(key: Optional[str]) -> str:
    return f"{key[:8]}…" if key else "no key yet"


def repository_text(repo) -> str:
    """One line of the repository list: its name, address, extensions and signing key (and its error)."""
    count = "?" if repo.extensions is None else str(repo.extensions)
    text = f"{repo.name} · {count} extension{'' if repo.extensions == 1 else 's'} · key {_short_key(repo.signing_key)}"
    text += f"\n{repo.url}"
    if repo.error:
        text += f"\n⚠ {repo.error}"
    return text


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
        self.host_downloads_edit = QLineEdit()
        self.host_downloads_edit.setPlaceholderText("/data/appdata/mangalist/extension-host-downloads")
        self.host_downloads_edit.setAccessibleName("Host downloads folder")
        hd = QWidget()
        hdl = QVBoxLayout(hd)
        hdl.setContentsMargins(0, 0, 0, 0)
        hdl.setSpacing(2)
        hdl.addWidget(self.host_downloads_edit)
        hdl.addWidget(label(HOST_DOWNLOADS_HELP, "muted", wrap=True))
        form.addRow("Host downloads folder:", hd)
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
        # the browser files stay on disk with the WebView off (transition audit D3): a button frees them
        self.btn_browser = button("Remove the browser files", tip=HOST_BROWSER_HELP)
        self.btn_browser.clicked.connect(lambda: self.remove_browser_files())
        self.browser_label = label("", "muted", wrap=True)
        self.browser_row = QWidget()
        bl = QVBoxLayout(self.browser_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addLayout(hbox(self.btn_browser, None))
        bl.addWidget(self.browser_label)
        self.browser_row.setVisible(False)
        self.browser_bytes = 0
        form.addRow("", self.browser_row)
        self.auto_update_check = checkbox("Update extensions automatically", False, tip=HOST_AUTO_UPDATE_HELP)
        auto = QWidget()
        al = QVBoxLayout(auto)
        al.setContentsMargins(0, 0, 0, 0)
        al.setSpacing(2)
        al.addWidget(self.auto_update_check)
        al.addWidget(label(HOST_AUTO_UPDATE_HELP, "muted", wrap=True))
        form.addRow("Updates:", auto)
        outer.addLayout(form)
        self.btn_test = button("Test connection")
        self.btn_test.clicked.connect(self.test_connection)
        outer.addLayout(hbox(self.btn_test, None))
        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        self.repos_editor = RepositoriesEditor(backend, self)
        outer.addWidget(self.repos_editor)
        outer.addStretch(1)
        self._config = ExtensionHostConfig()
        self._load()

    def _load(self) -> None:
        self._config = c = self._backend.load_extension_host()
        self.url_edit.setText(c.url)
        self.folder_edit.setText(c.download_dir)
        self.folder_edit.setPlaceholderText(c.downloads_path or "the host's own downloads folder")
        self.flare_edit.setText(c.flaresolverr_url)
        self.host_downloads_edit.setText(c.host_downloads)
        self.webview_check.setChecked(c.webview)
        getter = getattr(self._backend, "extension_auto_update", None)
        self.auto_update_check.setChecked(bool(getter()) if callable(getter) else False)
        self._measure_browser_files(c.webview)

    def _measure_browser_files(self, webview_on: bool) -> None:
        """With the WebView off (as saved), offer to remove its Chromium files when there are any (measured off the UI
        thread)."""
        size = getattr(self._backend, "webview_files_size", None)
        self.browser_row.setVisible(False)
        if webview_on or not callable(size):
            return
        self._calls.start(size, self._browser_measured, lambda _m: None)

    def _browser_measured(self, size: int) -> None:
        self.browser_bytes = int(size or 0)
        self.browser_row.setVisible(self.browser_bytes > 0)
        self.browser_label.setText(f"The WebView is off, but its Chromium bundle and cache still take "
                                   f"{self.browser_bytes / 1e6:.0f} MB in the host's data folder.")

    def remove_browser_files(self) -> bool:
        fn = getattr(self._backend, "remove_webview_files", None)
        if not callable(fn) or self._calls.busy():
            return False
        self.btn_browser.setEnabled(False)

        def done(freed: int) -> None:
            self.browser_row.setVisible(False)
            _say(self.status_label, f"The browser files were removed ({int(freed or 0) / 1e6:.0f} MB freed). Turning "
                                    "the WebView on downloads them again when a source needs them.", "ok")

        self._calls.start(fn, done, lambda message: _say(self.status_label, f"Not removed: {message}", "bad"),
                          lambda: self.btn_browser.setEnabled(True))
        return True

    def typed(self) -> ExtensionHostConfig:
        return ExtensionHostConfig(url=self.url_edit.text().strip(), download_dir=self.folder_edit.text().strip(),
                                   downloads_path=self._config.downloads_path, repo_url=self._config.repo_url,
                                   flaresolverr_url=self.flare_edit.text().strip(),
                                   webview=self.webview_check.isChecked(), repos=self._config.repos,
                                   host_downloads=self.host_downloads_edit.text().strip())

    def save(self) -> bool:
        from .downloads_backend import BackendError

        try:
            self._backend.save_extension_host(self.typed())
            setter = getattr(self._backend, "set_extension_auto_update", None)
            if callable(setter):
                setter(self.auto_update_check.isChecked())
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
        self.repos_editor.stop()
        self._calls.stop()


# --- the repository list --------------------------------------------------------------------------------------------

class RepositoriesEditor(QWidget):
    """The host's extension repositories: the list (name, extensions, signing key, address, last error), and Add / Remove
    buttons. Every change goes to the host at once (it checks a new one before listing it) and is remembered in
    MangaList's settings for the host's next start."""

    changed = Signal()

    def __init__(self, backend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("repositoriesEditor")
        self._backend = backend
        self._calls = _Calls()
        self.repos: list = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 8, 0, 0)
        outer.setSpacing(6)
        outer.addWidget(label("Extension repositories", "h3"))
        outer.addWidget(label(REPOS_HELP, "muted", wrap=True))
        self.list = QListWidget()
        self.list.setObjectName("repositoryList")
        self.list.setWordWrap(True)
        self.list.setSpacing(3)
        self.list.setMinimumHeight(110)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        outer.addWidget(self.list)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(DEFAULT_EXTENSION_REPO)
        self.url_edit.setAccessibleName("Repository address")
        self.url_edit.setClearButtonEnabled(True)
        self.url_edit.textChanged.connect(lambda _t: self._selection_changed())
        self.url_edit.returnPressed.connect(lambda: self.add())
        self.btn_add = button("Add repository", tip="Read this index.json and add it to the list (it must be a Mihon "
                                                    "extension repository with a signing key)")
        self.btn_add.clicked.connect(lambda: self.add())
        self.btn_remove = button("Remove", tip="Remove the selected repository (extensions installed from it stay "
                                               "installed and get no updates)")
        self.btn_remove.clicked.connect(lambda: self.remove())
        outer.addLayout(hbox(self.url_edit, self.btn_add, self.btn_remove))
        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        self._selection_changed()
        self.reload()

    def _usable(self) -> bool:
        return callable(getattr(self._backend, "extension_repositories", None))

    def reload(self) -> bool:
        if not self._usable() or self._calls.busy():
            return False
        _say(self.status_label, "Reading the host's repositories...")
        self._set_busy(True)
        self._calls.start(self._backend.extension_repositories, self.show_repos, self._unreadable,
                          lambda: self._set_busy(False))
        return True

    def _unreadable(self, message: str) -> None:
        from ..downloads.extensions_backend import repo_urls

        self.list.clear()
        for url in repo_urls(self._backend.load_extension_host()):        # what the host is started with
            self.list.addItem(QListWidgetItem(url))
        _say(self.status_label, f"The host's repositories could not be read: {message}. Start the host (or check its "
                                "address) to add or remove one.", "bad")

    def show_repos(self, repos) -> None:
        self.repos = list(repos)
        selected = self.selected_url()
        self.list.clear()
        for r in self.repos:
            item = QListWidgetItem(repository_text(r))
            item.setData(Qt.ItemDataRole.UserRole, r.url)
            item.setToolTip(f"{r.url}\nSigning key (SHA-256): {r.signing_key or 'learned at its first read'}")
            self.list.addItem(item)
            if r.url == selected:
                item.setSelected(True)
        n = len(self.repos)
        _say(self.status_label, f"{n} repositor{'y' if n == 1 else 'ies'}." if n else
             "No repository: add one (keiyoushi's is the usual one) to install extensions.", "" if n else "warn")
        self._selection_changed()

    def selected_url(self) -> Optional[str]:
        items = self.list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def select(self, url: str) -> bool:
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == url:
                self.list.setCurrentRow(i)
                return True
        return False

    def _selection_changed(self) -> None:
        idle = not self._calls.busy() and self._usable()
        self.btn_add.setEnabled(idle and bool(self.url_edit.text().strip()))
        self.btn_remove.setEnabled(idle and self.selected_url() is not None)

    def _set_busy(self, busy: bool) -> None:
        if busy:
            self.btn_add.setEnabled(False)
            self.btn_remove.setEnabled(False)
        else:
            self._selection_changed()

    def add(self, url: Optional[str] = None) -> bool:
        url = (url if url is not None else self.url_edit.text()).strip()
        if not url or self._calls.busy() or not self._usable():
            return False
        backend = self._backend
        _say(self.status_label, f"Reading {url}...")
        self._set_busy(True)

        def run():
            added = backend.add_extension_repository(url)
            return added, backend.extension_repositories()

        def done(result) -> None:
            added, repos = result
            self.url_edit.clear()
            self.show_repos(repos)
            self.select(added.url)
            count = "?" if added.extensions is None else added.extensions
            _say(self.status_label, f"Added {added.name}: {count} extension(s), signing key {_short_key(added.signing_key)}.",
                 "ok")
            self.changed.emit()

        self._calls.start(run, done, lambda message: _say(self.status_label, f"Not added: {message}", "bad"),
                          lambda: self._set_busy(self._calls.busy()))
        return True

    def remove(self) -> bool:
        url = self.selected_url()
        if url is None or self._calls.busy():
            return False
        backend = self._backend
        name = next((r.name for r in self.repos if r.url == url), url)
        _say(self.status_label, f"Removing {name}...")
        self._set_busy(True)

        def run():
            backend.remove_extension_repository(url)
            return backend.extension_repositories()

        def done(repos) -> None:
            self.show_repos(repos)
            _say(self.status_label, f"Removed {name}. Extensions installed from it stay installed and get no updates.",
                 "ok")
            self.changed.emit()

        self._calls.start(run, done, lambda message: _say(self.status_label, f"{name}: not removed: {message}", "bad"),
                          lambda: self._set_busy(self._calls.busy()))
        return True

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
        self.repo_label = label("Repositories: reading...", "muted", wrap=True, selectable=True)
        outer.addWidget(self.repo_label)
        self.repo_combo = QComboBox()
        self.repo_combo.setObjectName("repositoryFilter")
        self.repo_combo.setAccessibleName("Repository")
        self.repo_combo.addItem("All repositories", ALL_REPOS)
        self.repo_combo.currentIndexChanged.connect(lambda _i: self._render())
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
        outer.addLayout(hbox(self.filter_edit, self.repo_combo, self.installed_check, self.all_langs_check,
                             self.btn_reload))
        self.table = flat_table("extensionsTable", COLUMNS)
        resizable_columns(self.table, {0: 260, 1: 80, 2: 150, 3: 110})
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
        repos: Dict[str, str] = {}
        for r in self._rows:
            if r.repo and r.repo_name:
                repos.setdefault(r.repo, r.repo_name)
        self._fill_repo_filter(repos)
        listed = sum(1 for r in self._rows if r.repo_version)
        _say(self.status_label, f"{installed} installed" + (f", {updates} with an update" if updates else "")
             + f" · {listed} in {len(repos) or 1} repositor{'y' if len(repos) <= 1 else 'ies'}")
        self.repo_label.setText("Repositories: " + (", ".join(repos.values()) if repos else "the host's default")
                                + " (Built-in extensions > Edit adds or removes one)")
        self._render()

    def _fill_repo_filter(self, repos: Dict[str, str]) -> None:
        current = self.repo_combo.currentData()
        self.repo_combo.blockSignals(True)
        self.repo_combo.clear()
        self.repo_combo.addItem("All repositories", ALL_REPOS)
        for url, name in repos.items():
            self.repo_combo.addItem(name, url)
            self.repo_combo.setItemData(self.repo_combo.count() - 1, url, Qt.ItemDataRole.ToolTipRole)
        self.repo_combo.setCurrentIndex(max(0, self.repo_combo.findData(current)))
        self.repo_combo.blockSignals(False)
        self.repo_combo.setVisible(len(repos) > 1 or current not in (ALL_REPOS, None))

    def set_repository_filter(self, url: str) -> bool:
        index = self.repo_combo.findData(url)
        if index < 0:
            return False
        self.repo_combo.setCurrentIndex(index)
        return True

    def visible_rows(self) -> list:
        text = self.filter_edit.text().strip().casefold()
        repo = self.repo_combo.currentData() or ALL_REPOS
        out = []
        for r in self._rows:
            if repo != ALL_REPOS and r.repo != repo:
                continue
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
            name.setData(Qt.ItemDataRole.UserRole, (r.pkg, r.repo))
            name.setToolTip(f"{r.pkg}\n" + "\n".join(f"{n} ({lang}) · {sid}" for sid, n, lang in r.sources))
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, cell(r.lang or "-"))
            repo = cell(r.repo_name or "-")
            repo.setToolTip(r.repo)
            self.table.setItem(i, 2, repo)
            self.table.setItem(i, 3, cell(r.installed_version or r.repo_version))
            self.table.setItem(i, 4, cell(_status(r)))
            if selected is not None and (r.pkg, r.repo) == (selected.pkg, selected.repo):
                self.table.selectRow(i)
        self.btn_update_all.setEnabled(any(r.has_update for r in self._rows) and not self._calls.busy())
        self._selection_changed()

    def selected(self):
        items = self.table.selectedItems()
        if not items:
            return None
        key = tuple(self.table.item(items[0].row(), 0).data(Qt.ItemDataRole.UserRole) or ())
        return next((r for r in self._rows if (r.pkg, r.repo) == key), None)

    def select(self, pkg: str, repo: Optional[str] = None) -> bool:
        """Select *pkg*'s row (of *repo*, when the same package is in several repositories)."""
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).data(Qt.ItemDataRole.UserRole) or ("", "")
            if key[0] == pkg and (repo is None or key[1] == repo):
                self.table.selectRow(i)
                return True
        return False

    def _selection_changed(self) -> None:
        row = self.selected()
        idle = not self._calls.busy()
        self.btn_install.setEnabled(idle and row is not None and not row.installed and bool(row.repo_version)
                                    and not row.installed_from)
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
        pkg, name, repo = row.pkg, row.name, row.repo
        fn = {"install": (lambda p: backend.install_extension(p, repo or None)), "update": backend.update_extension,
              "uninstall": backend.uninstall_extension}[what]
        doing = {"install": "Installing", "update": "Updating", "uninstall": "Removing"}[what]
        done = {"install": "Installed", "update": "Updated", "uninstall": "Removed"}[what]
        where = f" from {row.repo_name}" if what == "install" and row.repo_name else ""
        _say(self.status_label, f"{doing} {name}{where}...")
        self._set_busy(True)

        def ok(_result) -> None:
            self.changed.emit()
            self._after = f"{done} {name}{where}."
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
