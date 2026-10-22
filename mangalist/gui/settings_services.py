"""Settings > Connected services: MangaPixer, qBittorrent and Suwayomi, each a card with its status, what it is for, where
it is, what uses it and Test / Edit. Edit opens the service's own form inside the section (the MangaPixer panel with its
libraries and root mapping; the qBittorrent form and the Suwayomi form (:class:`SuwayomiPanel`) with Save / Cancel).

**Suwayomi** (the Suwayomi MVP, 2026-10-10): the address, an optional basic-auth login (the password write-only, stored
like qBittorrent's and never logged), and the folder MangaList reads Suwayomi's downloads from (Suwayomi's ``downloads``
folder as MangaList sees it - chosen at install). Test connection shows Suwayomi's version and warns when "Download as
CBZ" or FlareSolverr is off in Suwayomi (FlareSolverr is set in Suwayomi itself, e.g. ``http://<unraid-ip>:8191``).

**Chapter downloads** (prototype/mihon-mangalist): a chooser at the top - "Suwayomi" (the default) or "Built-in
extensions", MangaList's own extension host - and the host's card (its status; Extensions, Test, Edit:
:mod:`mangalist.gui.extensions_page`). The card of the program not chosen says it is not in use. Switching is refused
while chapter downloads are still in progress with the other program (the backend says why).

The status is checked live the first time the section is shown (off the UI thread, with the stored login) and by Test.
Passwords and tokens stay write-only: the forms never show a stored one, the cards never mention one.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QLineEdit, QMessageBox, QStackedWidget, QVBoxLayout, QWidget,
)

from ..services.mangapixer import client as mpc
from ..store.mangapixer import MangaPixerCache
from .background import BackgroundCall, start_call
from .download_widgets import button, hbox, label
from .downloads_backend import BackendError, DownloadsBackend, SuwayomiSettingsView
from .mangapixer_dialog import ClientFactory, MangaPixerPanel
from .qbittorrent_dialog import QbittorrentPanel
from .settings_common import SectionPage, ServiceCard, back_link, clear_layout

LEAD = "Programs MangaList talks to. Each needs an address and a login; download sources need them."
MANGAPIXER_WHAT = ("Series links, volume lists and Completion for the folders MangaPixer knows; library scans after "
                   "filing.")
QBT_WHAT = "Downloads torrents in its own \"mangalist\" category."
SUWAYOMI_WHAT = "Downloads chapters from scanlation and official sites."
SUWAYOMI_INFO = ("Suwayomi is a self-hosted manga server that can download chapters from scanlation groups and official "
                 "sites (MangaDex and others). MangaList uses it for the \"Missing chapters\" group of the Download "
                 "tab: it finds the series (MangaDex by the id MangaPixer links), lists the missing chapters with their "
                 "scanlation groups and sends the ones you pick; finished chapters are filed into the series folder.")
SUWAYOMI_INTRO = ("MangaList asks Suwayomi-Server (GraphQL, tested with v2.4.2366) for chapters. Install the extensions "
                  "of your sources in Suwayomi first (MangaDex, ...); which of them MangaList may use is set under "
                  "Download sources.")
SUWAYOMI_FOLDER_HELP = ("Suwayomi's downloads folder (the one that holds \"mangas\") as MangaList sees it. MangaList "
                        "files each finished chapter from there into the series folder (a hard link on the same share, "
                        "else a verified copy), then Suwayomi deletes its own copy.")
SUWAYOMI_FLARESOLVERR_NOTE = ("Sources behind Cloudflare need FlareSolverr: set it in Suwayomi's own settings "
                              "(FlareSolverr on, URL e.g. http://<unraid-ip>:8191 - the box's address, not localhost).")
SUWAYOMI_PASSWORD_STORED = "A password is stored (hidden). Leave empty to keep it."
SUWAYOMI_PASSWORD_EMPTY = "Only when Suwayomi uses basic authentication"
EXT_WHAT = ("MangaList's own extension host: runs Mihon / Tachiyomi extensions and downloads chapters, without "
            "Suwayomi.")
EXT_INFO = ("The built-in extensions are MangaList's own alternative to Suwayomi for chapter downloads: a small program "
            "(the extension host) runs the same Mihon / Tachiyomi extensions Suwayomi uses - MangaDex, Weeb Central, "
            "MangaFire, ... - and downloads the chapters you send, as CBZ archives MangaList files into the series "
            "folder. Choose it under \"Chapter downloads\"; install the extensions under Extensions.")
CHOOSER_LEAD = "Chapter downloads through:"
BACKENDS = (("suwayomi", "Suwayomi"), ("extensions", "Built-in extensions"))
NOT_IN_USE = "Not in use"
SCAN_FORBIDDEN_NOTE = ("This token cannot request library scans, so MangaList cannot ask MangaPixer to rescan after "
                       "filing. Create a new token with \"Request library scans\" ticked and enter it under Edit.")


def _default_info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def mangapixer_lines(cache: MangaPixerCache) -> List[str]:
    """The card's detail lines: the address and token, the libraries, the roots' mapping (no secret in them)."""
    conn = cache.connection()
    lines = [f"{conn.base_url or 'no address'} · token: {'stored' if conn.has_token else 'none'}"]
    libs = cache.libraries()
    present = [lib for lib in libs if lib.present]
    if present:
        names = ", ".join(f"{lib.display_name}" + ("" if lib.default_kind else " (kind skipped)") for lib in present)
        lines.append(f"Libraries: {names}")
    names = {lib.id: lib.display_name for lib in libs}
    roots = cache.store.list_roots()
    mappings = cache.mappings()
    if roots and libs:
        parts = []
        for root in roots:
            m = mappings.get(root.id)
            if m is None:
                parts.append(f"{root.name or root.path} → not mapped yet")
            else:
                target = names.get(m.library_id, m.library_id) if m.library_id else "none"
                parts.append(f"{root.name or root.path} → {target}" + (" (manual)" if m.manual else ""))
        lines.append("Roots: " + "; ".join(parts))
    return lines


def scan_forbidden(cache: MangaPixerCache) -> bool:
    """MangaPixer answered 403 to a scan request: the token lacks the library:scan scope (the check exists from the
    scan-trigger release on; before it nothing is known and nothing is claimed)."""
    check = getattr(cache, "scan_forbidden_at", None)
    return bool(check and check())


class ServicesPage(SectionPage):
    def __init__(self, db, backend: Optional[DownloadsBackend], cache: Optional[MangaPixerCache],
                 parent: Optional[QWidget] = None, client_factory: Optional[ClientFactory] = None,
                 info: Optional[Callable[[QWidget, str, str], None]] = None):
        super().__init__("Connected services", LEAD, parent)
        self._db = db
        self._backend = backend
        self._cache = cache
        self._client_factory = client_factory
        self._info = info or _default_info
        self._calls: List[BackgroundCall] = []
        self._checked = False
        self._syncing = False
        self.mp_panel: Optional[MangaPixerPanel] = None
        self.qbt_panel: Optional[QbittorrentPanel] = None
        self.suwayomi_panel: Optional["SuwayomiPanel"] = None
        self.host_panel = None                  # ExtensionHostPanel while Edit is open
        self.extensions_panel = None            # ExtensionsPanel while Extensions is open
        self._ext_tested = False

        self.stack = QStackedWidget()
        self.body.addWidget(self.stack)
        cards = QWidget()
        cv = QVBoxLayout(cards)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(14)
        self.mp_card = ServiceCard("MangaPixer", MANGAPIXER_WHAT, "Matching, Automation")
        self.qbt_card = ServiceCard("qBittorrent", QBT_WHAT, "nyaa")
        self.suwayomi_card = ServiceCard("Suwayomi", SUWAYOMI_WHAT, "Suwayomi sources (chapters)")
        self.ext_card = ServiceCard("Built-in extensions", EXT_WHAT, "Suwayomi sources (chapters), when chosen")
        self.backend_combo = QComboBox()
        self.backend_combo.setObjectName("chapterBackend")
        for key, text in BACKENDS:
            self.backend_combo.addItem(text, key)
        self.backend_note = label("", "muted", wrap=True)
        self.chooser = QWidget()
        chooser = QVBoxLayout(self.chooser)
        chooser.setContentsMargins(0, 0, 0, 0)
        chooser.setSpacing(4)
        chooser.addLayout(hbox(label(CHOOSER_LEAD), self.backend_combo, None))
        chooser.addWidget(self.backend_note)
        for c in (self.mp_card, self.qbt_card, self.chooser, self.suwayomi_card, self.ext_card):
            cv.addWidget(c)
        cv.addStretch(1)
        self.stack.addWidget(cards)

        self.editor_page = QWidget()
        self.editor_layout = QVBoxLayout(self.editor_page)
        self.editor_layout.setContentsMargins(0, 0, 0, 0)
        self.editor_layout.setSpacing(10)
        self.stack.addWidget(self.editor_page)

        self.mp_card.btn_secondary.setText("Test")
        self.mp_card.btn_secondary.clicked.connect(self.test_mangapixer)
        # "Sync now" on the card itself, not only under Edit (owner, 2026-10-09)
        self.mp_sync = button("Sync now")
        self.mp_sync.setToolTip("Fetch MangaPixer's links, volume lists and libraries now, and pair the roots "
                                "(otherwise daily in the container)")
        self.mp_sync.clicked.connect(self.sync_mangapixer)
        self.mp_card.buttons.insertWidget(self.mp_card.buttons.indexOf(self.mp_card.btn_secondary), self.mp_sync)
        self.mp_card.btn_primary.clicked.connect(self.edit_mangapixer)
        self.qbt_card.btn_secondary.clicked.connect(self.test_qbittorrent)
        self.qbt_card.btn_primary.clicked.connect(self.edit_qbittorrent)
        self.suwayomi_info = button("What is it?", link=True)
        self.suwayomi_info.clicked.connect(lambda: self._info(self, "Suwayomi", SUWAYOMI_INFO))
        self.suwayomi_card.buttons.insertWidget(0, self.suwayomi_info)
        self.suwayomi_card.btn_secondary.clicked.connect(self.test_suwayomi)
        self.suwayomi_card.btn_primary.clicked.connect(self.edit_suwayomi)
        self.suwayomi_card.set_status("Not set up", "muted")
        self.ext_info = button("What is it?", link=True)
        self.ext_info.clicked.connect(lambda: self._info(self, "Built-in extensions", EXT_INFO))
        self.ext_card.buttons.insertWidget(0, self.ext_info)
        self.btn_extensions = button("Extensions", tip="Install, update or remove extensions; change a source's "
                                                       "settings")
        self.btn_extensions.clicked.connect(self.open_extensions)
        self.ext_card.buttons.insertWidget(self.ext_card.buttons.indexOf(self.ext_card.btn_secondary),
                                           self.btn_extensions)
        self.ext_card.btn_secondary.clicked.connect(self.test_extension_host)
        self.ext_card.btn_primary.clicked.connect(self.edit_extension_host)
        self.ext_card.set_status(NOT_IN_USE, "muted")
        self.backend_combo.activated.connect(self._backend_chosen)
        self.refresh()

    # --- the cards ---------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_mangapixer()
        self._refresh_qbittorrent()
        self._refresh_suwayomi()
        self._refresh_extensions()

    # --- the chapter backend (prototype/mihon-mangalist) -------------------------------------------------------

    def _ext_backend(self) -> bool:
        return self._backend is not None and callable(getattr(self._backend, "chapter_backend", None))

    def chapter_backend(self) -> str:
        return self._backend.chapter_backend() if self._ext_backend() else "suwayomi"

    def builtin(self) -> bool:
        return self.chapter_backend() == "extensions"

    def _refresh_extensions(self) -> None:
        card = self.ext_card
        self.chooser.setVisible(self._ext_backend())
        card.setVisible(self._ext_backend())
        if not self._ext_backend():
            return
        chosen = self.chapter_backend()
        self.backend_combo.setCurrentIndex(max(0, self.backend_combo.findData(chosen)))
        config = self._backend.load_extension_host()
        folder = config.download_dir or config.downloads_path or "the host's own (learned at Test)"
        card.detail_label.setText(f"{config.url} · download folder {folder}")
        on = chosen == "extensions"
        if not on:
            card.set_status(NOT_IN_USE, "muted")
            card.set_note("Chapter downloads go through Suwayomi; choose \"Built-in extensions\" above to use it.",
                          "muted")
        elif card.badge.text() == NOT_IN_USE:
            card.set_status("Not checked", "muted")
            card.set_note("")
        if on and self.suwayomi_card.badge.text() != NOT_IN_USE:
            self.suwayomi_card.set_status(NOT_IN_USE, "muted")
            self.suwayomi_card.set_note("Chapter downloads go through the built-in extensions.", "muted")
        elif not on and self.suwayomi_card.badge.text() == NOT_IN_USE:
            self.suwayomi_card.set_status("Not checked", "muted")
            self.suwayomi_card.set_note("")
            self._refresh_suwayomi()

    def _backend_chosen(self, _index: int = 0) -> bool:
        key = self.backend_combo.currentData()
        if not self._ext_backend() or key == self.chapter_backend():
            return False
        from .downloads_backend import BackendError as _BackendError

        try:
            self._backend.set_chapter_backend(key)
        except _BackendError as exc:
            self.backend_note.setText(f"Not switched: {exc}")
            from .download_style import set_tone

            set_tone(self.backend_note, "bad")
            self._refresh_extensions()
            return False
        self.backend_note.setText("")
        self.downloads_changed.emit()
        self.refresh()
        if key == "extensions":
            self.test_extension_host()
        else:
            self.test_suwayomi()
        return True

    def choose_backend(self, key: str) -> bool:
        """Pick a chapter backend as the owner would (tests)."""
        self.backend_combo.setCurrentIndex(self.backend_combo.findData(key))
        return self._backend_chosen()

    def test_extension_host(self) -> bool:
        if not self._ext_backend():
            return False
        backend = self._backend
        card = self.ext_card
        card.btn_secondary.setEnabled(False)
        card.set_status("Checking...", "muted")
        card.set_note("")
        self._spawn(lambda: backend.test_extension_host(), self._ext_ok, self._ext_failed)
        return True

    def _ext_ok(self, check) -> None:
        card = self.ext_card
        card.btn_secondary.setEnabled(True)
        card.set_status(f"Connected {check.version}".strip(), "ok")
        notes = check.notes()
        detail = f"{check.extensions} extension(s), {check.sources} source(s)"
        card.set_note(detail + (". " + " ".join(notes) if notes else ""), "warn" if notes else "muted")
        config = self._backend.load_extension_host()
        card.detail_label.setText(f"{config.url} · download folder {check.files_from or '?'}")
        if not self.builtin():
            card.set_status(NOT_IN_USE, "muted")

    def _ext_failed(self, message: str) -> None:
        card = self.ext_card
        card.btn_secondary.setEnabled(True)
        card.set_status("Not connected" if self.builtin() else NOT_IN_USE, "bad" if self.builtin() else "muted")
        card.set_note(message + (" In the container the host starts by itself within a minute of choosing it; on a "
                                 "desktop, start it and check its address (Edit).") if self.builtin() else message,
                      "bad" if self.builtin() else "muted")

    def edit_extension_host(self):
        if not self._ext_backend():
            return None
        from .extensions_page import ExtensionHostPanel

        self._clear_editor()
        panel = ExtensionHostPanel(self._backend, self.editor_page)
        panel.saved_changes.connect(self._host_saved)
        self.host_panel = panel
        save = button("Save", primary=True)
        save.clicked.connect(panel.save)
        cancel = button("Cancel")
        cancel.clicked.connect(self.close_editor)
        self._open_editor("Built-in extensions", panel, cancel, save)
        return panel

    def _host_saved(self) -> None:
        self.downloads_changed.emit()
        self.close_editor()
        self.test_extension_host()

    def open_extensions(self):
        if not self._ext_backend():
            return None
        from .extensions_page import ExtensionsPanel

        self._clear_editor()
        panel = ExtensionsPanel(self._backend, self.editor_page)
        panel.changed.connect(self.downloads_changed.emit)
        self.extensions_panel = panel
        self._open_editor("Extensions", panel)
        return panel

    def _suwayomi_backend(self) -> bool:
        return self._backend is not None and callable(getattr(self._backend, "load_suwayomi", None))

    def _refresh_suwayomi(self) -> None:
        card = self.suwayomi_card
        if not self._suwayomi_backend():
            card.set_status("Downloads off" if self._backend is None else "Not available", "muted")
            card.detail_label.setText("Downloads are switched off in this installation (MANGALIST_DOWNLOADS)."
                                      if self._backend is None else "This build cannot talk to Suwayomi.")
            card.btn_secondary.setEnabled(False)
            card.btn_primary.setEnabled(False)
            return
        view = self._backend.load_suwayomi()
        card.btn_primary.setEnabled(True)
        card.btn_primary.setText("Edit" if view.base_url else "Set up")
        card.detail_label.setText(f"{view.base_url or 'no address'} · download folder "
                                  f"{view.download_dir or 'not set'}")
        card.btn_secondary.setEnabled(bool(view.base_url))
        if not view.base_url:
            card.set_status("Not set up", "muted")
            card.set_note("")
        elif not view.download_dir:
            card.set_status("No download folder", "warn")
            card.set_note("Set the folder MangaList reads Suwayomi's downloads from (Edit), or finished chapters "
                          "cannot be filed.")
        elif card.badge.text() in ("Not set up", "Downloads off", "Not available", "No download folder"):
            card.set_status("Not checked", "muted")

    def _refresh_mangapixer(self) -> None:
        card = self.mp_card
        if self._cache is None:
            card.set_status("Not available", "muted")
            card.detail_label.setText("-")
            card.btn_secondary.setEnabled(False)
            card.btn_primary.setEnabled(False)
            self.mp_sync.setEnabled(False)
            return
        conn = self._cache.connection()
        card.detail_label.setText("\n".join(mangapixer_lines(self._cache)))
        card.btn_secondary.setEnabled(bool(conn.base_url and conn.has_token))
        self.mp_sync.setEnabled(bool(conn.base_url and conn.has_token) and not conn.token_rejected_at
                                and not self._syncing)
        if not conn.base_url or not conn.has_token:
            card.set_status("Not set up", "muted")
        elif conn.token_rejected_at:
            card.set_status("Token refused", "bad")
        elif card.badge.text() in ("Not set up", "Token refused", "Not available"):
            card.set_status("Not checked", "muted")
        card.set_note(SCAN_FORBIDDEN_NOTE if scan_forbidden(self._cache) else "")

    def _refresh_qbittorrent(self) -> None:
        card = self.qbt_card
        if self._backend is None:
            card.set_status("Downloads off", "muted")
            card.detail_label.setText("Downloads are switched off in this installation (MANGALIST_DOWNLOADS).")
            card.btn_secondary.setEnabled(False)
            card.btn_primary.setEnabled(False)
            return
        try:
            settings = self._backend.load_settings()
        except BackendError as exc:
            card.set_status("Unreadable", "bad")
            card.set_note(f"Could not read the saved settings: {exc}", "bad")
            return
        card.detail_label.setText(f"{settings.base_url or 'no address'} · download folder {settings.save_path}")
        card.btn_secondary.setEnabled(bool(settings.base_url))
        if not settings.base_url:
            card.set_status("Not set up", "muted")
        elif card.badge.text() in ("Not set up", "Downloads off", "Unreadable"):
            card.set_status("Not checked", "muted")

    # --- live checks ----------------------------------------------------------------------------------------

    def on_show(self) -> None:
        if self._checked:
            return
        self._checked = True
        self.test_mangapixer()
        self.test_qbittorrent()
        if self.builtin():
            self.test_extension_host()
        else:
            self.test_suwayomi()

    def _spawn(self, fn, on_done, on_error) -> None:
        made: list = []
        call = start_call(fn, on_done, on_error,
                          lambda: self._calls.remove(made[0]) if made and made[0] in self._calls else None)
        made.append(call)
        self._calls.append(call)

    def test_mangapixer(self) -> bool:
        cache = self._cache
        if cache is None:
            return False
        conn = cache.connection()
        if not conn.base_url or not conn.has_token:
            return False
        factory = self._client_factory or (lambda url, token, verify: mpc.MangaPixerClient(url, token, verify=verify))
        client = factory(conn.base_url, cache.token(), conn.verify)
        self.mp_card.btn_secondary.setEnabled(False)
        self.mp_card.set_status("Checking...", "muted")

        def run():
            try:
                client.ping()
                return ("ok", len(client.libraries()))
            except mpc.TokenRejected:
                return ("refused", "MangaPixer refused the token (wrong, revoked or expired). Enter a new one under Edit.")
            except mpc.MangaPixerError as exc:                   # the client's own messages carry no secret
                return ("failed", f"Connection failed: {exc}")
            finally:
                client.close()

        self._spawn(run, self._mp_result, lambda message: self._mp_result(("failed", message)))
        return True

    def sync_mangapixer(self) -> bool:
        """Sync MangaPixer now, in the background (what Edit > Sync now does); the roots are paired again after it."""
        cache = self._cache
        if cache is None or self._syncing:
            return False
        conn = cache.connection()
        if not conn.base_url or not conn.has_token or conn.token_rejected_at:
            return False
        factory = self._client_factory or (lambda url, token, verify: mpc.MangaPixerClient(url, token, verify=verify))
        client = factory(conn.base_url, cache.token(), conn.verify)
        self._syncing = True
        self.mp_sync.setEnabled(False)
        self.mp_sync.setText("Syncing...")
        self.mp_card.set_status("Syncing...", "muted")

        def run():
            from ..services.mangapixer.sync import sync_all

            try:
                return sync_all(cache, client=client, manual=True)
            finally:
                client.close()

        self._spawn(run, self._mp_synced, lambda message: self._mp_synced(None, message))
        return True

    def _mp_synced(self, result, error: str = "") -> None:
        self._syncing = False
        self.mp_sync.setText("Sync now")
        self._refresh_mangapixer()
        # the badge said "Syncing..." until the next check (owner, 2026-10-11: "why does it still say syncing?")
        if result is None or getattr(result, "status", "ok") == "error":
            self.mp_card.set_status("Sync failed", "bad")
            self.mp_card.set_note(f"Sync failed: {error or getattr(result, 'message', '')}", "bad")
            return
        if getattr(result, "token_rejected", False):
            self.mp_card.set_status("Token refused", "bad")
            self.mp_card.set_note("MangaPixer refused the token (wrong, revoked or expired). Enter a new one under Edit.")
            return
        self.mp_card.set_status("Connected", "ok")
        if not scan_forbidden(self._cache):                     # that warning stays visible
            self.mp_card.set_note(f"Sync: {getattr(result, 'message', 'done')}", "muted")
        self.mangapixer_changed.emit()

    def _mp_result(self, answer) -> None:
        kind, detail = answer
        card = self.mp_card
        card.btn_secondary.setEnabled(True)
        if kind == "ok":
            card.set_status("Connected", "ok")
            card.set_note(SCAN_FORBIDDEN_NOTE if scan_forbidden(self._cache) else "")
            return
        card.set_status("Token refused" if kind == "refused" else "Not reachable", "bad")
        card.set_note(str(detail), "bad")

    def test_qbittorrent(self) -> bool:
        backend = self._backend
        if backend is None:
            return False
        try:
            settings = backend.load_settings()
        except BackendError:
            return False
        if not settings.base_url:
            return False
        self.qbt_card.btn_secondary.setEnabled(False)
        self.qbt_card.set_status("Checking...", "muted")
        self.qbt_card.set_note("")
        self._spawn(lambda: backend.test_connection(settings, None), self._qbt_ok, self._qbt_failed)
        return True

    def _qbt_ok(self, version: str) -> None:
        self.qbt_card.set_status(f"Connected {version}".strip(), "ok")
        self.qbt_card.btn_secondary.setEnabled(True)
        self.qbt_card.set_note("")

    def _qbt_failed(self, message: str) -> None:
        self.qbt_card.set_status("Not connected", "bad")
        self.qbt_card.btn_secondary.setEnabled(True)
        self.qbt_card.set_note(message, "bad")

    def test_suwayomi(self) -> bool:
        if not self._suwayomi_backend():
            return False
        backend = self._backend
        view = backend.load_suwayomi()
        if not view.base_url:
            return False
        self.suwayomi_card.btn_secondary.setEnabled(False)
        self.suwayomi_card.set_status("Checking...", "muted")
        self.suwayomi_card.set_note("")
        self._spawn(lambda: backend.test_suwayomi(view, None), self._suwayomi_ok, self._suwayomi_failed)
        return True

    def _suwayomi_ok(self, check) -> None:
        card = self.suwayomi_card
        card.btn_secondary.setEnabled(True)
        card.set_status(f"Connected {check.version}".strip(), "ok")
        notes = check.notes()
        card.set_note(" ".join(notes), "warn" if notes else "muted")
        if self.builtin():
            card.set_status(NOT_IN_USE, "muted")
            card.set_note(f"Reachable ({check.version}), but chapter downloads go through the built-in extensions.",
                          "muted")

    def _suwayomi_failed(self, message: str) -> None:
        self.suwayomi_card.set_status("Not connected", "bad")
        self.suwayomi_card.btn_secondary.setEnabled(True)
        self.suwayomi_card.set_note(message, "bad")

    # --- the editors ------------------------------------------------------------------------------------------

    def _open_editor(self, title: str, widget: QWidget, *buttons) -> None:
        back = back_link("Connected services")
        back.clicked.connect(self.close_editor)
        self.editor_layout.addLayout(hbox(back, None, *buttons))
        self.editor_layout.addWidget(label(title, "h3"))
        self.editor_layout.addWidget(widget, 1)
        self.stack.setCurrentWidget(self.editor_page)

    def edit_mangapixer(self) -> Optional[MangaPixerPanel]:
        if self._cache is None:
            return None
        self._clear_editor()
        panel = MangaPixerPanel(self._cache, self.editor_page, self._client_factory)
        panel.changed.connect(self.mangapixer_changed)
        self.mp_panel = panel
        self._open_editor("MangaPixer", panel)
        return panel

    def edit_qbittorrent(self) -> Optional[QbittorrentPanel]:
        if self._backend is None:
            return None
        self._clear_editor()
        panel = QbittorrentPanel(self._backend, self.editor_page)
        panel.saved_changes.connect(self._qbt_saved)
        self.qbt_panel = panel
        save = button("Save", primary=True)
        save.clicked.connect(panel.save)
        cancel = button("Cancel")
        cancel.clicked.connect(self.close_editor)
        self._open_editor("qBittorrent", panel, cancel, save)
        return panel

    def edit_suwayomi(self) -> Optional["SuwayomiPanel"]:
        if not self._suwayomi_backend():
            return None
        self._clear_editor()
        panel = SuwayomiPanel(self._backend, self.editor_page)
        panel.saved_changes.connect(self._suwayomi_saved)
        self.suwayomi_panel = panel
        save = button("Save", primary=True)
        save.clicked.connect(panel.save)
        cancel = button("Cancel")
        cancel.clicked.connect(self.close_editor)
        self._open_editor("Suwayomi", panel, cancel, save)
        return panel

    def _suwayomi_saved(self) -> None:
        self.downloads_changed.emit()
        self.close_editor()
        self.test_suwayomi()

    def _qbt_saved(self) -> None:
        self.downloads_changed.emit()
        self.close_editor()
        self.test_qbittorrent()

    def close_editor(self) -> None:
        was_mangapixer = self.mp_panel is not None
        self._clear_editor()
        self.stack.setCurrentIndex(0)
        self.refresh()
        if was_mangapixer:
            self.test_mangapixer()

    def _clear_editor(self) -> None:
        for panel in (self.mp_panel, self.qbt_panel, self.suwayomi_panel, self.host_panel, self.extensions_panel):
            if panel is not None:
                panel.stop()
        self.mp_panel = self.qbt_panel = self.suwayomi_panel = self.host_panel = self.extensions_panel = None
        clear_layout(self.editor_layout)

    def stop(self) -> None:
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
        self._clear_editor()


class SuwayomiPanel(QWidget):
    """Suwayomi's connection form: address, user name, password (write-only), the download folder MangaList reads, and
    Test connection (off the UI thread, against the values as typed; nothing is stored before Save)."""

    saved_changes = Signal()

    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("suwayomiPanel")
        self._backend = backend
        self._call: Optional[BackgroundCall] = None
        self._view = SuwayomiSettingsView()
        self.saved = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(label(SUWAYOMI_INTRO, wrap=True))
        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://192.168.1.10:4567")
        form.addRow("Address:", self.url_edit)
        self.user_edit = QLineEdit()
        self.user_edit.setPlaceholderText("Only when Suwayomi uses basic authentication")
        form.addRow("Username:", self.user_edit)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password:", self.password_edit)
        self.folder_edit = QLineEdit()
        self.folder_edit.setPlaceholderText("/data/appdata/suwayomi/downloads")
        folder = QWidget()
        fl = QVBoxLayout(folder)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(2)
        fl.addWidget(self.folder_edit)
        fl.addWidget(label(SUWAYOMI_FOLDER_HELP, "muted", wrap=True))
        form.addRow("Download folder:", folder)
        outer.addLayout(form)
        outer.addWidget(label(SUWAYOMI_FLARESOLVERR_NOTE, "muted", wrap=True))
        self.btn_test = button("Test connection")
        self.btn_test.clicked.connect(self.test_connection)
        outer.addLayout(hbox(self.btn_test, None))
        self.status_label = label("", wrap=True, selectable=True)
        outer.addWidget(self.status_label)
        outer.addStretch(1)
        self._load()

    def _load(self) -> None:
        try:
            self._view = self._backend.load_suwayomi()
        except BackendError as exc:
            self._say(f"Could not read the saved settings: {exc}", "bad")
        v = self._view
        self.url_edit.setText(v.base_url)
        self.user_edit.setText(v.username)
        self.password_edit.clear()
        self.password_edit.setPlaceholderText(SUWAYOMI_PASSWORD_STORED if v.has_password else SUWAYOMI_PASSWORD_EMPTY)
        self.folder_edit.setText(v.download_dir)

    def typed(self) -> SuwayomiSettingsView:
        url = self.url_edit.text().strip()
        if not url:
            raise ValueError("Enter Suwayomi's address, for example http://192.168.1.10:4567")
        return SuwayomiSettingsView(base_url=url, username=self.user_edit.text().strip(),
                                    has_password=self._view.has_password, download_dir=self.folder_edit.text().strip())

    def _say(self, text: str, tone: str = "") -> None:
        from .download_style import set_tone

        set_tone(self.status_label, tone)
        self.status_label.setText(text)

    def save(self) -> bool:
        try:
            view = self.typed()
            self._backend.save_suwayomi(view, self.password_edit.text() or None)
        except (ValueError, BackendError) as exc:
            self._say(f"Could not save: {exc}", "bad")
            return False
        self.saved = True
        self.password_edit.clear()             # never shown again
        self._view = self._backend.load_suwayomi()
        self.password_edit.setPlaceholderText(SUWAYOMI_PASSWORD_STORED if self._view.has_password
                                              else SUWAYOMI_PASSWORD_EMPTY)
        self._say("Saved.", "ok")
        self.saved_changes.emit()
        return True

    def test_connection(self) -> bool:
        if self._call is not None:
            return False
        try:
            view = self.typed()
        except ValueError as exc:
            self._say(str(exc), "bad")
            return False
        password = self.password_edit.text() or None
        backend = self._backend
        self.btn_test.setEnabled(False)
        self._say("Testing the connection...")
        self._call = start_call(lambda: backend.test_suwayomi(view, password), self._tested,
                                lambda message: self._say(f"Connection failed: {message}", "bad"), self._test_done)
        return True

    def _tested(self, check) -> None:
        notes = check.notes()
        self._say(f"Connected: Suwayomi {check.version}." + (" " + " ".join(notes) if notes else ""),
                  "warn" if notes else "ok")

    def _test_done(self) -> None:
        self._call = None
        self.btn_test.setEnabled(True)

    def stop(self) -> None:
        if self._call is not None:
            self._call.abandon()
