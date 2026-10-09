"""Settings > Connected services: MangaPixer, qBittorrent and Suwayomi (not set up yet), each a card with its status, what
it is for, where it is, what uses it and Test / Edit. Edit opens the service's own form inside the section (the
MangaPixer panel with its libraries and root mapping; the qBittorrent form with Save / Cancel).

The status is checked live the first time the section is shown (off the UI thread, with the stored login) and by Test.
Passwords and tokens stay write-only: the forms never show a stored one, the cards never mention one.
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtWidgets import QMessageBox, QStackedWidget, QVBoxLayout, QWidget

from ..services.mangapixer import client as mpc
from ..store.mangapixer import MangaPixerCache
from .background import BackgroundCall, start_call
from .download_widgets import button, hbox, label
from .downloads_backend import BackendError, DownloadsBackend
from .mangapixer_dialog import ClientFactory, MangaPixerPanel
from .qbittorrent_dialog import QbittorrentPanel
from .settings_common import SectionPage, ServiceCard, back_link, clear_layout

LEAD = "Programs MangaList talks to. Each needs an address and a login; download sources need them."
MANGAPIXER_WHAT = ("Series links, volume lists and Completion for the folders MangaPixer knows; library scans after "
                   "filing.")
QBT_WHAT = "Downloads torrents in its own \"mangalist\" category."
SUWAYOMI_WHAT = "Downloads chapters from scanlation and official sites."
SUWAYOMI_INFO = ("Suwayomi is a self-hosted manga server that can download chapters from scanlation groups and official "
                 "sites (MangaDex and others). MangaList will use it for the \"Missing chapters\" group of the Download "
                 "tab. Connecting it comes in a later version.")
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
        self.mp_panel: Optional[MangaPixerPanel] = None
        self.qbt_panel: Optional[QbittorrentPanel] = None

        self.stack = QStackedWidget()
        self.body.addWidget(self.stack)
        cards = QWidget()
        cv = QVBoxLayout(cards)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(14)
        self.mp_card = ServiceCard("MangaPixer", MANGAPIXER_WHAT, "Matching, Automation")
        self.qbt_card = ServiceCard("qBittorrent", QBT_WHAT, "nyaa")
        self.suwayomi_card = ServiceCard("Suwayomi", SUWAYOMI_WHAT, "Suwayomi sources (chapters)")
        for c in (self.mp_card, self.qbt_card, self.suwayomi_card):
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
        self.mp_card.btn_primary.clicked.connect(self.edit_mangapixer)
        self.qbt_card.btn_secondary.clicked.connect(self.test_qbittorrent)
        self.qbt_card.btn_primary.clicked.connect(self.edit_qbittorrent)
        self.suwayomi_card.btn_secondary.setText("What is it?")
        self.suwayomi_card.btn_secondary.clicked.connect(lambda: self._info(self, "Suwayomi", SUWAYOMI_INFO))
        self.suwayomi_card.btn_primary.setText("Set up")
        self.suwayomi_card.btn_primary.setEnabled(False)
        self.suwayomi_card.btn_primary.setToolTip("Connecting Suwayomi comes in a later version")
        self.suwayomi_card.set_status("Not set up", "muted")
        self.refresh()

    # --- the cards ---------------------------------------------------------------------------------------------

    def refresh(self) -> None:
        self._refresh_mangapixer()
        self._refresh_qbittorrent()

    def _refresh_mangapixer(self) -> None:
        card = self.mp_card
        if self._cache is None:
            card.set_status("Not available", "muted")
            card.detail_label.setText("-")
            card.btn_secondary.setEnabled(False)
            card.btn_primary.setEnabled(False)
            return
        conn = self._cache.connection()
        card.detail_label.setText("\n".join(mangapixer_lines(self._cache)))
        card.btn_secondary.setEnabled(bool(conn.base_url and conn.has_token))
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
        for panel in (self.mp_panel, self.qbt_panel):
            if panel is not None:
                panel.stop()
        self.mp_panel = self.qbt_panel = None
        clear_layout(self.editor_layout)

    def stop(self) -> None:
        for call in list(self._calls):
            call.abandon()
        self._calls.clear()
        self._clear_editor()
