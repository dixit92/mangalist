"""qBittorrent settings: the Web UI address and login, certificate checking, where torrents are saved, and
"Remove Completed".

The password is typed into a masked field and never shown again once saved: the field stays empty and its
placeholder says one is stored ("leave empty to keep"). It is never logged and never part of a message.
"Test connection" runs off the UI thread against the values as typed (an empty password field means "the stored
one"); the connection is not saved until Save.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .background import BackgroundCall, start_call
from .downloads_backend import DEFAULT_SAVE_PATH, BackendError, DownloadsBackend, QbtSettings

PASSWORD_STORED_HINT = "A password is stored (hidden). Leave empty to keep it."
PASSWORD_EMPTY_HINT = "qBittorrent Web UI password"
DOWNLOAD_FOLDER_HELP = ("qBittorrent's own copy while it downloads and seeds - not your library. MangaList links the "
                        "volumes into the series folder; Remove Completed deletes this copy.")
REMOVE_COMPLETED_HELP = ("Like Sonarr / Radarr: once qBittorrent has stopped a torrent at its own seed goal and "
                         "MangaList has checked the volumes in your library, MangaList asks qBittorrent to remove "
                         "the torrent and its downloaded copy (only torrents in the \"mangalist\" category).")


def normalize_url(text: str) -> str:
    """``http://host:8080`` (scheme required, no trailing slash); ValueError with a readable reason otherwise."""
    url = text.strip().rstrip("/")
    if not url:
        raise ValueError("Enter the qBittorrent Web UI address, for example http://192.168.1.10:8080")
    if not url.lower().startswith(("http://", "https://")) or len(url.split("://", 1)[1]) == 0:
        raise ValueError("The address must start with http:// or https://")
    return url


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #666;")
    return label


def _with_hint(field: QWidget, hint: QLabel) -> QWidget:
    """A field with its grey explanation right below it, as one form row (a word-wrapped label in a row of its own
    gets a gap above it from QFormLayout)."""
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    lay.addWidget(field)
    lay.addWidget(hint)
    return box


class QbittorrentDialog(QDialog):
    def __init__(self, backend: DownloadsBackend, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("qBittorrent")
        self.resize(640, 420)
        self._backend = backend
        self._call: Optional[BackgroundCall] = None
        self._settings = QbtSettings()
        self.saved = False
        self._build_ui()
        self._load()

    # --- UI ------------------------------------------------------------------------------------------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        intro = QLabel("MangaList sends the releases you pick to qBittorrent through its Web UI, in its own "
                       "\"mangalist\" category. Enable the Web UI in qBittorrent's options first.")
        intro.setWordWrap(True)
        outer.addWidget(intro)

        form = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("http://192.168.1.10:8080")
        form.addRow("Web UI address:", self.url_edit)
        self.user_edit = QLineEdit()
        form.addRow("Username:", self.user_edit)
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Password:", self.password_edit)
        self.tls_check = QCheckBox("Verify the TLS certificate (https addresses)")
        form.addRow("", self.tls_check)
        self.save_path_edit = QLineEdit()
        self.save_path_edit.setPlaceholderText(DEFAULT_SAVE_PATH)
        self.save_path_edit.setToolTip("qBittorrent's save path for the \"mangalist\" category, as qBittorrent sees "
                                       "it. It must be on the same mount as your library for hard links.")
        self.save_path_help = _hint(DOWNLOAD_FOLDER_HELP)
        form.addRow("Download folder:", _with_hint(self.save_path_edit, self.save_path_help))
        self.remove_check = QCheckBox("Remove Completed")
        self.remove_help = _hint(REMOVE_COMPLETED_HELP)
        form.addRow("", _with_hint(self.remove_check, self.remove_help))
        outer.addLayout(form)

        row = QHBoxLayout()
        self.btn_test = QPushButton("Test connection")
        self.btn_test.clicked.connect(self.test_connection)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        outer.addLayout(row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        outer.addWidget(self.status_label)
        outer.addStretch(1)

        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        self.button_box = box
        outer.addWidget(box)

    # --- data ----------------------------------------------------------------------------------------

    def _load(self) -> None:
        try:
            self._settings = self._backend.load_settings()
        except BackendError as exc:
            self._settings = QbtSettings()
            self._say(f"Could not read the saved settings: {exc}", error=True)
        s = self._settings
        self.url_edit.setText(s.base_url)
        self.user_edit.setText(s.username)
        self.password_edit.clear()
        self.password_edit.setPlaceholderText(PASSWORD_STORED_HINT if s.has_password else PASSWORD_EMPTY_HINT)
        self.tls_check.setChecked(s.verify_tls)
        self.save_path_edit.setText(s.save_path or DEFAULT_SAVE_PATH)
        self.remove_check.setChecked(s.remove_completed)

    def _typed(self) -> QbtSettings:
        """The settings as typed (raises ValueError for an unusable address or an empty save path)."""
        save_path = self.save_path_edit.text().strip()
        if not save_path:
            raise ValueError("Enter the download folder (for example " + DEFAULT_SAVE_PATH + ")")
        return replace(self._settings, base_url=normalize_url(self.url_edit.text()),
                       username=self.user_edit.text().strip(), verify_tls=self.tls_check.isChecked(),
                       save_path=save_path, remove_completed=self.remove_check.isChecked())

    def _typed_password(self) -> Optional[str]:
        return self.password_edit.text() or None

    def _say(self, text: str, *, error: bool = False, ok: bool = False) -> None:
        self.status_label.setStyleSheet("color: #b71c1c;" if error else "color: #2e7d32;" if ok else "")
        self.status_label.setText(text)

    # --- actions -------------------------------------------------------------------------------------

    def save(self) -> bool:
        try:
            settings = self._typed()
        except ValueError as exc:
            self._say(str(exc), error=True)
            return False
        password = self._typed_password()
        try:
            self._backend.save_settings(settings, password)
        except BackendError as exc:
            self._say(f"Could not save: {exc}", error=True)
            return False
        self.saved = True
        self._settings = replace(settings, has_password=settings.has_password or bool(password))
        self.url_edit.setText(settings.base_url)
        self.password_edit.clear()            # never shown again
        self.password_edit.setPlaceholderText(PASSWORD_STORED_HINT if self._settings.has_password
                                              else PASSWORD_EMPTY_HINT)
        self._say("Saved.", ok=True)
        return True

    def accept(self) -> None:
        if self.save():
            super().accept()

    def test_connection(self) -> bool:
        if self._call is not None:
            return False
        try:
            settings = self._typed()
        except ValueError as exc:
            self._say(str(exc), error=True)
            return False
        password = self._typed_password()
        self.btn_test.setEnabled(False)
        self._say("Testing the connection...")
        backend = self._backend
        self._call = start_call(lambda: backend.test_connection(settings, password), self._on_tested,
                                self._on_test_failed, self._on_call_finished)
        return True

    def _on_tested(self, version: str) -> None:
        self._say(f"Connected. qBittorrent {version}".rstrip() + ".", ok=True)

    def _on_test_failed(self, message: str) -> None:
        self._say(f"Connection failed: {message}", error=True)

    def _on_call_finished(self) -> None:
        self._call = None
        self.btn_test.setEnabled(True)

    def done(self, result: int) -> None:
        if self._call is not None:
            self._call.abandon()
        super().done(result)


def open_qbittorrent_dialog(parent: Optional[QWidget], backend: DownloadsBackend) -> bool:
    """Open the qBittorrent settings (modal). True when the owner saved."""
    dlg = QbittorrentDialog(backend, parent)
    dlg.exec()
    return dlg.saved
