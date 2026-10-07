"""Settings dialog of the MangaPixer source: address, API token, certificate choice, test, libraries,
the root -> library mapping (automatic, with a manual override per root), and "Sync now".

The token is typed into a password field that shows the characters only while it is being edited. Once
saved it is never shown again: the field stays empty, and its placeholder says a token is stored. The
token is never logged.

The main window opens it with :func:`open_mangapixer_dialog` (wired by the integrator).
"""

from __future__ import annotations

from typing import Callable, List, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..services.mangapixer import client as mpc
from ..services.mangapixer import mapping as mp_map
from ..services.mangapixer.sync import SyncResult, sync_all
from ..store.mangapixer import MangaPixerCache, kind_is_default
from .tables import cell, resizable_columns

ClientFactory = Callable[[str, Optional[str], object], mpc.MangaPixerClient]

TOKEN_STORED_HINT = "A token is stored (hidden). Paste a new one to replace it."
TOKEN_EMPTY_HINT = "mpx_... (MangaPixer > Administration > API tokens)"
AUTO = "__auto__"
NONE = "__none__"


def _default_client_factory(base_url: str, token: Optional[str], verify) -> mpc.MangaPixerClient:
    return mpc.MangaPixerClient(base_url, token, verify=verify)



class _SyncWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)

    def __init__(self, cache: MangaPixerCache, client: mpc.MangaPixerClient, force_full: bool):
        super().__init__()
        self._cache, self._client, self._force_full = cache, client, force_full
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        try:
            result = sync_all(self._cache, client=self._client, force_full=self._force_full, manual=True,
                              progress=lambda d, t, n: self.progress.emit(d, t, n),
                              should_stop=lambda: self._stop)
        except Exception as exc:  # noqa: BLE001 - shown to the owner, never crashes the GUI
            result = SyncResult(status="error", message=f"sync failed: {type(exc).__name__}")
        finally:
            self._client.close()
        self.finished.emit(result)


class MangaPixerDialog(QDialog):
    def __init__(self, cache: MangaPixerCache, parent: Optional[QWidget] = None,
                 client_factory: Optional[ClientFactory] = None):
        super().__init__(parent)
        self.setWindowTitle("MangaPixer source")
        self.resize(900, 640)
        self._cache = cache
        self._client_factory = client_factory or _default_client_factory
        self._thread: Optional[QThread] = None
        self._worker: Optional[_SyncWorker] = None
        self.last_result: Optional[SyncResult] = None
        self._build_ui()
        self._load()

    # --- UI ------------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        intro = QLabel("MangaList reads what MangaPixer (1.33.0 or newer) already knows about your series through "
                       "its read-only metadata export. Folders MangaPixer links are not looked up again.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        conn = QGroupBox("Connection")
        form = QFormLayout(conn)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://192.168.1.10:8080")
        form.addRow("Server address:", self.url_edit)
        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        form.addRow("API token:", self.token_edit)
        self.insecure_check = QCheckBox("Accept any certificate (a self-signed MangaPixer certificate; not checked)")
        form.addRow("", self.insecure_check)
        ca_row = QHBoxLayout()
        self.ca_edit = QLineEdit()
        self.ca_edit.setPlaceholderText("optional: the CA file of a self-signed certificate")
        self.btn_ca = QPushButton("Browse")
        self.btn_ca.clicked.connect(self._browse_ca)
        ca_row.addWidget(self.ca_edit, 1)
        ca_row.addWidget(self.btn_ca)
        form.addRow("CA file:", ca_row)
        btns = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save_connection)
        self.btn_test = QPushButton("Test connection")
        self.btn_test.clicked.connect(self.test_connection)
        self.btn_forget = QPushButton("Forget token")
        self.btn_forget.clicked.connect(self.forget_token)
        btns.addWidget(self.btn_save)
        btns.addWidget(self.btn_test)
        btns.addWidget(self.btn_forget)
        btns.addStretch(1)
        form.addRow("", btns)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("", self.status_label)
        outer.addWidget(conn)

        libs = QGroupBox("Libraries")
        lv = QVBoxLayout(libs)
        self.lib_table = QTableWidget(0, 5)
        self.lib_table.setHorizontalHeaderLabels(["Library", "Kind", "Items", "Used", "Last sync"])
        resizable_columns(self.lib_table)
        self.lib_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        lv.addWidget(self.lib_table)
        outer.addWidget(libs)

        maps = QGroupBox("Roots")
        mv = QVBoxLayout(maps)
        self.map_table = QTableWidget(0, 5)
        self.map_table.setHorizontalHeaderLabels(["Root", "MangaPixer library", "Inside (trail)", "Matched",
                                                  "Unmatched"])
        resizable_columns(self.map_table)
        self.map_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.map_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.map_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.map_table.itemSelectionChanged.connect(self._on_root_selected)
        mv.addWidget(self.map_table)
        override = QHBoxLayout()
        self.lib_combo = QComboBox()
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setPlaceholderText("folder inside the library, e.g. Shonen (optional)")
        self.any_kind_check = QCheckBox("Use although its kind is skipped")
        self.btn_apply_map = QPushButton("Apply")
        self.btn_apply_map.clicked.connect(self.apply_override)
        override.addWidget(QLabel("Override:"))
        override.addWidget(self.lib_combo, 1)
        override.addWidget(self.prefix_edit, 1)
        override.addWidget(self.any_kind_check)
        override.addWidget(self.btn_apply_map)
        mv.addLayout(override)
        outer.addWidget(maps)

        sync_row = QHBoxLayout()
        self.btn_sync = QPushButton("Sync now")
        self.btn_sync.clicked.connect(lambda: self.sync_now())
        self.full_check = QCheckBox("Full sync")
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.sync_label = QLabel("")
        sync_row.addWidget(self.btn_sync)
        sync_row.addWidget(self.full_check)
        sync_row.addWidget(self.progress, 1)
        sync_row.addWidget(self.sync_label, 1)
        outer.addLayout(sync_row)

        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        outer.addWidget(box)

    def _browse_ca(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "CA file", "", "Certificates (*.pem *.crt *.cer);;All files (*)")
        if path:
            self.ca_edit.setText(path)

    # --- data ----------------------------------------------------------------------------------------

    def _load(self) -> None:
        conn = self._cache.connection()
        self.url_edit.setText(conn.base_url)
        self.token_edit.clear()
        self.token_edit.setPlaceholderText(TOKEN_STORED_HINT if conn.has_token else TOKEN_EMPTY_HINT)
        self.insecure_check.setChecked(not conn.verify_tls)
        self.ca_edit.setText(conn.ca_file or "")
        self.btn_forget.setEnabled(conn.has_token)
        if conn.token_rejected_at:
            self.status_label.setText("MangaPixer refused the stored token (wrong, revoked or expired). Enter a new "
                                      "token; MangaList does not try again on its own.")
        self._refresh_tables()

    def _verify(self):
        ca = self.ca_edit.text().strip()
        if ca:
            return ca
        return not self.insecure_check.isChecked()

    def save_connection(self) -> bool:
        try:
            url = mpc.normalize_base_url(self.url_edit.text())
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return False
        token = self.token_edit.text().strip()
        kwargs = {"base_url": url, "verify_tls": not self.insecure_check.isChecked(),
                  "ca_file": self.ca_edit.text().strip() or None}
        if token:
            kwargs["token"] = token
        self._cache.set_connection(**kwargs)
        self.url_edit.setText(url)
        self.token_edit.clear()           # never shown again
        self.status_label.setText("Saved.")
        self._load()
        return True

    def forget_token(self) -> None:
        self._cache.set_connection(token=None)
        self.status_label.setText("Token removed.")
        self._load()

    def _client(self) -> Optional[mpc.MangaPixerClient]:
        try:
            url = mpc.normalize_base_url(self.url_edit.text())
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return None
        token = self.token_edit.text().strip() or self._cache.token()
        return self._client_factory(url, token, self._verify())

    def test_connection(self) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            ping = client.ping()
            libs = client.libraries()
        except mpc.TokenRejected:
            self.status_label.setText("MangaPixer refused the token (wrong, revoked or expired). Check it and try "
                                      "again; repeated wrong tokens lock this address out for a few minutes.")
            return False
        except mpc.MangaPixerError as exc:
            self.status_label.setText(f"Connection failed: {exc}")
            return False
        finally:
            client.close()
        used = sum(1 for lib in libs if kind_is_default(lib.kind))
        self.status_label.setText(f"Connected. MangaPixer time {ping.server_time}; {len(libs)} libraries "
                                  f"({used} used by default).")
        return True

    def _refresh_tables(self) -> None:
        libs = self._cache.libraries()
        to_sync = {lib.id for lib in self._cache.libraries_to_sync()}
        self.lib_table.setRowCount(len(libs))
        for row, lib in enumerate(libs):
            state = self._cache.sync_state(lib.id)
            last = state.last_sync_at or "never"
            if state.last_status == "error":
                last += f" - {state.last_error}"
            name = lib.display_name + ("" if lib.present else " (gone from MangaPixer - re-map)")
            cells = [name, lib.kind or "-", str(self._cache.item_count(lib.id)),
                     "yes" if lib.id in to_sync else "no (kind)", last]
            for col, text in enumerate(cells):
                self.lib_table.setItem(row, col, cell(text))
        self.lib_table.resizeColumnsToContents()

        self.lib_combo.clear()
        self.lib_combo.addItem("Automatic", AUTO)
        self.lib_combo.addItem("Do not use MangaPixer", NONE)
        for lib in libs:
            if lib.present:
                self.lib_combo.addItem(f"{lib.display_name} ({lib.kind or 'no kind'})", lib.id)

        names = {lib.id: lib.display_name for lib in libs}
        roots = self._cache.store.list_roots()
        mappings = self._cache.mappings()
        self._root_ids: List[int] = []
        self.map_table.setRowCount(len(roots))
        for row, root in enumerate(roots):
            self._root_ids.append(root.id)
            m = mappings.get(root.id)
            if m is None:
                lib_text, prefix, matched, unmatched = "not mapped yet (sync first)", "", "", ""
            else:
                lib_text = names.get(m.library_id, m.library_id) if m.library_id else "none"
                lib_text += " (manual)" if m.manual else " (automatic)"
                prefix = "/".join(m.prefix)
                matched = "" if m.matched is None else str(m.matched)
                unmatched = "" if m.unmatched is None else str(m.unmatched)
            for col, text in enumerate([root.name, lib_text, prefix, matched, unmatched]):
                self.map_table.setItem(row, col, cell(text))
        self.map_table.resizeColumnsToContents()
        last = self._cache.last_sync_at()
        self.sync_label.setText(f"Last sync: {last}" if last else "Not synced yet")

    def _selected_root(self) -> Optional[int]:
        rows = self.map_table.selectionModel().selectedRows() if self.map_table.selectionModel() else []
        if not rows:
            return None
        return self._root_ids[rows[0].row()]

    def _on_root_selected(self) -> None:
        root_id = self._selected_root()
        m = self._cache.mapping(root_id) if root_id is not None else None
        if m is None or not m.manual:
            self.lib_combo.setCurrentIndex(self.lib_combo.findData(AUTO))
            self.prefix_edit.clear()
            self.any_kind_check.setChecked(False)
            return
        idx = self.lib_combo.findData(m.library_id if m.library_id else NONE)
        self.lib_combo.setCurrentIndex(max(idx, 0))
        self.prefix_edit.setText("/".join(m.prefix))
        self.any_kind_check.setChecked(m.any_kind)

    def apply_override(self) -> bool:
        root_id = self._selected_root()
        if root_id is None:
            self.status_label.setText("Select a root first.")
            return False
        choice = self.lib_combo.currentData()
        if choice == AUTO:
            mp_map.clear_manual_mapping(self._cache, root_id)
        else:
            prefix = [p for p in self.prefix_edit.text().replace("\\", "/").split("/") if p.strip()]
            mp_map.set_manual_mapping(self._cache, root_id, None if choice == NONE else choice, prefix,
                                      any_kind=self.any_kind_check.isChecked())
        row = self._root_ids.index(root_id)
        self._refresh_tables()
        self.map_table.selectRow(row)
        return True

    # --- sync ----------------------------------------------------------------------------------------

    def sync_now(self) -> bool:
        if self._thread is not None:
            return False
        if not self.save_connection():
            return False
        client = self._client()
        if client is None:
            return False
        self.btn_sync.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.sync_label.setText("Syncing...")
        self._worker = _SyncWorker(self._cache, client, self.full_check.isChecked())
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_sync_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()
        return True

    @property
    def syncing(self) -> bool:
        return self._thread is not None

    def _on_progress(self, done: int, total: int, name: str) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(min(done, total))
        else:
            self.progress.setRange(0, 0)
        self.sync_label.setText(f"{name}: {done} items")

    def _on_sync_finished(self, result: SyncResult) -> None:
        self.last_result = result
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait()
        self._thread = None
        self._worker = None
        self.btn_sync.setEnabled(True)
        self.progress.setVisible(False)
        self._load()
        if result.token_rejected:
            self.status_label.setText("MangaPixer refused the token (wrong, revoked or expired). Enter a new token; "
                                      "MangaList does not try again on its own.")
        else:
            self.status_label.setText(f"Sync: {result.message}")

    def reject(self) -> None:
        if self._worker is not None:
            self._worker.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(10_000)
        super().reject()


def open_mangapixer_dialog(parent: Optional[QWidget] = None, cache: Optional[MangaPixerCache] = None) -> int:
    """Open the MangaPixer source settings (modal). Returns the dialog's result code."""
    if cache is None:
        from ..services.mangapixer import open_cache

        cache = open_cache()
    dlg = MangaPixerDialog(cache, parent)
    return dlg.exec()
