"""Settings > Download sources, Matching, Automation and Logging.

**Download sources** - where releases come from. nyaa (volumes; needs qBittorrent) with the options the search already
supports (English / raw, hide light novels, only trusted uploaders) and two shown fixed because that is how the search
always works (Digital first, no 0-seeder releases); **Chapter sources** (chapters; Suwayomi or the built-in extensions,
whichever is chosen - the chooser is on this card too, and is the same setting as Connected services'): the sources the
chosen program has installed, read from it when the section is shown - which MangaList may use (ticked) and in what order it
tries them (MangaDex first by default); stored by the source id. The list is shared by both programs: editing it keeps the
ticked sources the other program has installed (:func:`merge_allowed`), and it is read again when the program is switched.
The status chip says what was found when the sources were read (Connected / Starting / Not reachable), not that an address
is stored. **Matching** - where series information comes from.
**Automation** - Remove Completed, "ask MangaPixer to rescan after filing", the default automatic-download mode and
replaced chapters (the schedules moved to Scheduled tasks, 2026-10-15). **Logging** - the level of the log files (and
optionally of one area), their size, and the log folder.

Every switch is stored as soon as it is changed (there is no Save here), in the library database's settings.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, Mapping, Optional

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QLineEdit,
    QMessageBox,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import log_config, paths, upgrades

from ..downloads.options import (
    KEY_MU_AUTOSTART,
    KEY_PARTIAL_DOWNLOADS,
    KEY_SCAN_AFTER_FILING,
    NyaaOptions,
    get_flag,
    load_nyaa_options,
    save_nyaa_options,
    set_flag,
)
from .automation_rules import MODE_HELP, MODE_LABELS, MODES, has_automation
from .background import describe_error, start_call
from .chapter_words import CARD_TITLE, backend_key, label_of, reword
from .download_rules import (
    held_size_text,
)
from .download_style import set_prop, set_tone
from .download_widgets import button, card, checkbox, hbox, label, pill
from .spin_arrows import pad_spin
from .downloads_backend import BackendError, DownloadsBackend
from .links import open_folder as open_folder_default
from .settings_common import SectionPage
from .settings_services import BACKENDS, CHOOSER_LEAD, SCAN_FORBIDDEN_NOTE, scan_forbidden
from . import start_window
from .start_window import StartWindow
from .shell import SECTION_SERVICES

SECTION_LOGGING = "logging"

SOURCES_LEAD = "Where releases come from. A source works only when the service it needs is connected."
MATCHING_LEAD = "Where series information comes from. Not download sources."
AUTOMATION_LEAD = "How automatic downloads and filing behave. When things run is under Scheduled tasks."
LOGGING_LEAD = "What MangaList writes to its log files, and where they are."
SUWAYOMI_EXPLAIN = ("The sources installed in Suwayomi (from their extensions). Tick the ones MangaList may use, in "
                    "the order it tries them: MangaDex first - it is found by the MangaDex id MangaPixer links; the "
                    "others by title, and you confirm the match once per series. The scanlation group is chosen per "
                    "series in the Download tab.")
BUILTIN_EXPLAIN = ("The sources of the extensions installed in MangaList's built-in extension host (install them under "
                   "Connected services > Built-in extensions > Extensions). Tick the ones MangaList may use, in the "
                   "order it tries them: MangaDex first - it is found by the MangaDex id MangaPixer links; the others "
                   "by title, and you confirm the match once per series. The scanlation group is chosen per series in "
                   "the Download tab.")
SHARED_NOTE = ("The ticks and the order are shared by Suwayomi and the built-in extensions; a source only the other "
               "program has installed keeps its place while you edit this list.")
CHIP_NOT_CHECKED, CHIP_CHECKING = "Not checked", "Checking..."
CHIP_CONNECTED, CHIP_STARTING, CHIP_UNREACHABLE = "Connected", "Starting...", "Not reachable"


def merge_allowed(stored, listed_ids, ticked) -> list:
    """The allowed source ids to store after the list shown (*listed_ids*: every row the chosen program installs, ticked
    or not) was edited to *ticked* (in the list's order): the sources the program has NOT installed are in *stored* only,
    and they stay - each right after the source that preceded it - so switching programs does not lose them (B7)."""
    listed = set(listed_ids)
    after: dict = {}
    prev = None
    for sid in stored or ():
        if sid in listed:
            prev = sid
        else:
            after.setdefault(prev, []).append(sid)
    out: list = list(after.pop(None, []))
    for sid in ticked:
        out.append(sid)
        out.extend(after.pop(sid, []))
    for rest in after.values():
        out.extend(rest)
    seen: set = set()
    return [sid for sid in out if not (sid in seen or seen.add(sid))]
BUDGET_HELP = ("Counts every download MangaList has sent and not yet removed: downloading, waiting to be filed, and "
               "seeding (a partial download counts only its selected files). A send that would go over it waits in a "
               "queue and goes to qBittorrent by itself once Remove Completed has made room. Lowering it removes "
               "nothing; new sends then wait. Chapter downloads (Suwayomi) do not count: they do not seed.")
AUTOMATIC_DOWNLOADS_LEAD = ("Each series is Off, Notify or Auto - set per series wherever it shows (the List, the To get "
                            "list, Found: Automatic downloads), or for a whole library in Library > Edit. Series you "
                            "have not set follow their library's default, else this one.")
AUTOMATIC_DOWNLOADS_NOTE = ("The find pass runs on its schedule under Scheduled tasks (and with Find now in the Download "
                            "tab) and looks only at series with gaps - the Download tab's To get list.")
AUTOMATIC_DOWNLOADS_OFF = "Not available: downloads are switched off in this installation."
FIXED_DIGITAL = "Always on: the ranking puts Digital releases first."
FIXED_SEEDERS = "Always on: releases nobody is seeding are never shown."


class SourcesPage(SectionPage):
    #: The owner chose another chapter backend on this card (the key): the dialog tells Connected services.
    backend_switched = Signal(str)

    def __init__(self, db, backend: Optional[DownloadsBackend], parent: Optional[QWidget] = None):
        super().__init__("Download sources", SOURCES_LEAD, parent)
        self._db = db
        self._backend = backend
        self._loading = True
        self._start = StartWindow()             # the host was just chosen: a failed read is "starting" for a while
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._retry_sources)

        nyaa = card("true")
        nv = QVBoxLayout(nyaa)
        nv.setContentsMargins(20, 16, 20, 16)
        nv.setSpacing(10)
        self.nyaa_badge = pill("Ready", "ok")
        self.on_check = checkbox("On")
        nv.addLayout(hbox(label("nyaa", "name"), label("Volumes · needs qBittorrent", "muted"), self.nyaa_badge,
                          None, self.on_check, spacing=10))
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        self.english_check = checkbox("English-translated releases")
        self.raw_check = checkbox("Raw (Japanese) releases")
        self.novels_check = checkbox("Hide light novels")
        self.digital_check = checkbox("Digital releases first", True, enabled=False, tip=FIXED_DIGITAL)
        self.trusted_check = checkbox("Only trusted uploaders")
        self.seeders_check = checkbox("Hide releases without seeders", True, enabled=False, tip=FIXED_SEEDERS)
        for i, box in enumerate((self.english_check, self.raw_check, self.novels_check, self.digital_check,
                                 self.trusted_check, self.seeders_check)):
            grid.addWidget(box, i // 2, i % 2)
        nv.addLayout(grid)
        self.partial_check = checkbox("Download only the missing volumes of a pack", True,
                                      tip="New sends start with \"Only the missing volumes\" ticked; untick it for one "
                                          "send to download the whole pack. A torrent that skips files seeds only what "
                                          "it downloaded.")
        nv.addWidget(self.partial_check)
        nv.addWidget(label("qBittorrent skips the files of a pack that hold no missing volume. You choose again "
                           "for every send.", "muted", wrap=True))
        self.body.addWidget(nyaa)

        self.body.addWidget(self._suwayomi_card())
        self.body.addWidget(self._budget_card())

        self.refresh()
        for box in (self.on_check, self.english_check, self.raw_check, self.novels_check, self.trusted_check):
            box.toggled.connect(self._changed)
        self.partial_check.toggled.connect(self._partial_changed)
        self.budget_spin.valueChanged.connect(self._budget_changed)

    # Suwayomi sources (the Suwayomi MVP, 2026-10-10): the installed sources as Suwayomi lists them, ticked = allowed,
    # in order. Read off the UI thread when the section is shown; every change is stored at once.
    def _suwayomi_card(self) -> QWidget:
        from PySide6.QtWidgets import QAbstractItemView, QListWidget

        suwayomi = card("true")
        sv = QVBoxLayout(suwayomi)
        sv.setContentsMargins(20, 16, 20, 16)
        sv.setSpacing(10)
        self.btn_suwayomi = button("Set up Suwayomi")
        self.btn_suwayomi.clicked.connect(lambda: self.section_requested.emit(SECTION_SERVICES))
        self.suwayomi_badge = pill("Suwayomi not set up", "warn")
        self.btn_sources_reload = button("Reload", link=True, tip="Read the installed sources from Suwayomi again")
        self.btn_sources_reload.clicked.connect(self.load_suwayomi_sources)
        self.btn_sources_reset = button("Reset to default", link=True,
                                        tip="Use MangaDex alone, in your nyaa languages (English unless Raw is ticked)")
        self.btn_sources_reset.clicked.connect(self.reset_suwayomi_sources)
        self.sources_program = label("", "muted")
        sv.addLayout(hbox(label(CARD_TITLE, "name"), self.sources_program,
                          self.suwayomi_badge, None, self.btn_sources_reset, self.btn_sources_reload, self.btn_suwayomi,
                          spacing=10))
        # the chooser of Connected services (owner, 2026-10-24: "there should be a switch to get chapters either using
        # the Suwayomi method or the in-house method"): one setting, two places, the same guard
        self.backend_combo = QComboBox()
        self.backend_combo.setObjectName("chapterBackend")
        for key, text in BACKENDS:
            self.backend_combo.addItem(text, key)
        self.backend_note = label("", "muted", wrap=True)
        self.chooser = QWidget()
        chooser = QVBoxLayout(self.chooser)
        chooser.setContentsMargins(0, 0, 0, 0)
        chooser.setSpacing(4)
        chooser.addLayout(hbox(label(CHOOSER_LEAD), self.backend_combo, None, spacing=8))
        chooser.addWidget(self.backend_note)
        sv.addWidget(self.chooser)
        self.backend_combo.activated.connect(self._backend_chosen)
        self.sources_explain = label(SUWAYOMI_EXPLAIN, "lead", wrap=True)
        sv.addWidget(self.sources_explain)
        self.sources_list = QListWidget()
        self.sources_list.setObjectName("suwayomiSources")
        self.sources_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sources_list.setMinimumHeight(140)
        self.sources_list.itemChanged.connect(self._sources_changed)
        self.btn_up = button("Move up")
        self.btn_up.clicked.connect(lambda: self._move_source(-1))
        self.btn_down = button("Move down")
        self.btn_down.clicked.connect(lambda: self._move_source(1))
        sv.addWidget(self.sources_list)
        # owner, 2026-10-10: "Seventy-two rows to find five in is clumsy" - the sources in nyaa's languages (English by
        # default) and the ones in use; the rest behind this switch
        self.all_langs_check = checkbox("Show all languages")
        self.all_langs_check.toggled.connect(lambda _on: self._render_sources())
        sv.addWidget(self.all_langs_check)
        self._source_rows: list = []
        self.sources_note = label("", "muted", wrap=True)
        sv.addLayout(hbox(self.btn_up, self.btn_down, self.sources_note, None, spacing=8))
        sv.addWidget(label(SHARED_NOTE, "muted", wrap=True))
        self.suwayomi_rows = suwayomi
        self._sources_call = None
        self._loading_sources = False
        self._chip = CHIP_NOT_CHECKED
        self._show_suwayomi_state()
        return suwayomi

    def _suwayomi_ready(self) -> bool:
        ready = getattr(self._backend, "suwayomi_ready", None) if self._backend is not None else None
        try:
            return bool(ready()) if callable(ready) else False
        except Exception:  # noqa: BLE001
            return False

    def _builtin(self) -> bool:
        """Chapter downloads go through the built-in extensions (prototype/mihon-mangalist)."""
        chosen = getattr(self._backend, "chapter_backend", None) if self._backend is not None else None
        try:
            return callable(chosen) and chosen() == "extensions"
        except Exception:  # noqa: BLE001
            return False

    # --- the chosen program, its words and its status chip --------------------------------------------------------

    def _w(self, text: str) -> str:
        return reword(self._backend, text) or ""

    def _chooser_available(self) -> bool:
        return (self._backend is not None and callable(getattr(self._backend, "chapter_backend", None))
                and callable(getattr(self._backend, "set_chapter_backend", None)))

    def _set_chip(self, text: str, kind: str) -> None:
        self._chip = text
        self.suwayomi_badge.setText(text)
        set_prop(self.suwayomi_badge, "badge", kind)

    def _show_suwayomi_state(self) -> None:
        from .download_style import set_prop

        ready = self._suwayomi_ready()
        builtin = self._builtin()
        self.sources_program.setText(f"Chapters · {label_of(self._backend)}")
        self.sources_explain.setText(BUILTIN_EXPLAIN if builtin else SUWAYOMI_EXPLAIN)
        self.btn_sources_reload.setToolTip(self._w("Read the installed sources from Suwayomi again"))
        self.chooser.setVisible(self._chooser_available())
        self.backend_combo.setCurrentIndex(max(0, self.backend_combo.findData(backend_key(self._backend))))
        if hasattr(self, "budget_help"):
            self.budget_help.setText(self._w(BUDGET_HELP))
        if not ready:
            # "Ready" used to mean only "an address is stored"; without one there is nothing to check
            self._set_chip("Not set up" if builtin else "Suwayomi not set up", "warn")
        elif self._chip in ("Not set up", "Suwayomi not set up"):
            self._set_chip(CHIP_NOT_CHECKED, "muted")
        self.btn_suwayomi.setText(self._w("Set up Suwayomi"))
        self.btn_suwayomi.setVisible(not ready)
        for w in (self.sources_list, self.btn_up, self.btn_down, self.btn_sources_reload, self.btn_sources_reset):
            w.setEnabled(ready)
        if not ready:
            self.sources_note.setText(self._w("Connect Suwayomi first (Settings > Connected services)."))

    def _backend_chosen(self, _index: int = 0) -> bool:
        """The chooser: the same setting and the same guard as Connected services' (the backend refuses while chapter
        downloads are in progress with the other program)."""
        key = self.backend_combo.currentData()
        if not self._chooser_available() or key == backend_key(self._backend):
            return False
        try:
            self._backend.set_chapter_backend(key)
        except BackendError as exc:
            self.backend_note.setText(f"Not switched: {exc}")
            set_tone(self.backend_note, "bad")
            self._show_suwayomi_state()
            return False
        self.backend_note.setText("")
        set_tone(self.backend_note, "")
        self.downloads_changed.emit()
        self.note_backend_switched(key)
        self.backend_switched.emit(key)
        return True

    def choose_backend(self, key: str) -> bool:
        """Pick a chapter backend as the owner would (tests)."""
        self.backend_combo.setCurrentIndex(self.backend_combo.findData(key))
        return self._backend_chosen()

    def note_backend_switched(self, key: str) -> None:
        """The chapter backend is *key* now (chosen here or under Connected services): the list of the other program is
        dropped at once and the chosen program's is read - the host with a minute's grace, since it is only starting (B7,
        A6)."""
        self._retry_timer.stop()
        if self._sources_call is not None:
            self._sources_call.abandon()
            self._sources_call = None
        self._source_rows = []
        self._loading_sources = True
        self.sources_list.clear()
        self._loading_sources = False
        self.all_langs_check.setVisible(False)
        self.backend_note.setText("")
        if key == "extensions":
            self._start.open()
        else:
            self._start.close()
        self._set_chip(CHIP_NOT_CHECKED, "muted")
        self._show_suwayomi_state()
        self.load_suwayomi_sources()

    def load_suwayomi_sources(self) -> bool:
        """Read the installed sources from the chosen program (off the UI thread). That read is also the status check: the
        chip says Connected only once the program answered."""
        loader = getattr(self._backend, "suwayomi_sources", None) if self._backend is not None else None
        if not callable(loader) or not self._suwayomi_ready() or self._sources_call is not None:
            return False
        key = backend_key(self._backend)
        self._retry_timer.stop()
        self.sources_note.setText(self._w("Reading the sources from Suwayomi..."))
        if self._chip != CHIP_STARTING:
            self._set_chip(CHIP_CHECKING, "muted")
        holder: list = []

        def done() -> None:
            if holder and self._sources_call is holder[0]:
                self._sources_call = None

        call = start_call(loader, lambda sources: self._sources_read(key, sources),
                          lambda message: self._sources_failed(key, message), done)
        holder.append(call)
        self._sources_call = call
        return True

    def _sources_read(self, key: str, sources) -> None:
        if key != backend_key(self._backend):          # the owner switched meanwhile: that answer is the other program's
            return
        self._start.close()
        self._set_chip(CHIP_CONNECTED, "ok")
        self.show_sources(sources)

    def _sources_failed(self, key: str, message: str) -> None:
        if key != backend_key(self._backend):
            return
        if self._builtin() and self._start.active():
            self._set_chip(CHIP_STARTING, "muted")
            self.sources_note.setText("The extension host is starting (it starts by itself within a minute of choosing "
                                      "it); MangaList keeps trying.")
            self._retry_timer.start(start_window.RETRY_MS)
            return
        self._set_chip(CHIP_UNREACHABLE, "bad")
        self.sources_note.setText(f"Could not read them: {message}")

    def _retry_sources(self) -> None:
        if self._builtin():
            self.load_suwayomi_sources()

    def show_sources(self, sources) -> None:
        """``[(SuwayomiSource, allowed)]`` in order (the backend's answer)."""
        self._source_rows = list(sources)
        self._render_sources()

    def _source_shown(self, source, allowed: bool) -> bool:
        """A source in the list: every one with "Show all languages", else the ones in nyaa's languages and any in use
        (a ticked source never disappears)."""
        if allowed or self.all_langs_check.isChecked():
            return True
        return source.lang in load_nyaa_options(self._db).source_languages()

    def _render_sources(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QListWidgetItem

        if self.sources_list.count():                       # keep the order and ticks shown so far
            ticked = set(self.allowed_source_ids())
            order = [self.sources_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.sources_list.count())]
            rank = {sid: i for i, sid in enumerate(order)}
            self._source_rows = sorted(((src, src.id in ticked) for src, _a in self._source_rows),
                                       key=lambda row: rank.get(row[0].id, len(rank)))
        sources = self._source_rows
        shown = [(src, allowed) for src, allowed in sources if self._source_shown(src, allowed)]
        hidden = len(sources) - len(shown)
        self.all_langs_check.setText(f"Show all languages ({hidden} more)" if hidden else "Show all languages")
        self.all_langs_check.setVisible(bool(hidden) or self.all_langs_check.isChecked())
        self._loading_sources = True
        self.sources_list.clear()
        for source, allowed in shown:
            item = QListWidgetItem(f"{source.display_name}  ·  {source.lang}")
            item.setData(Qt.ItemDataRole.UserRole, source.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if allowed else Qt.CheckState.Unchecked)
            item.setToolTip(f"Source id {source.id} · {source.extension or 'extension unknown'}")
            self.sources_list.addItem(item)
        self._loading_sources = False
        n = sum(1 for _s, allowed in shown if allowed)
        self.sources_note.setText(f"{n} of {len(sources)} in use" if sources else
                                  ("The extension host has no sources yet: install extensions (Settings > Connected "
                                   "services > Built-in extensions > Extensions; MangaDex first)." if self._builtin() else
                                   "Suwayomi has no sources yet: install extensions in Suwayomi (MangaDex first)."))

    def reset_suwayomi_sources(self) -> bool:
        """Forget the ticks and the order: MangaDex alone in the nyaa languages, then read the list again."""
        reset = getattr(self._backend, "reset_suwayomi_sources", None) if self._backend is not None else None
        if not callable(reset):
            return False
        reset()
        self.sources_list.clear()                           # the new order comes from the backend, not the old rows
        self.downloads_changed.emit()
        return self.load_suwayomi_sources()

    def allowed_source_ids(self) -> list:
        from PySide6.QtCore import Qt

        out = []
        for i in range(self.sources_list.count()):
            item = self.sources_list.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.append(item.data(Qt.ItemDataRole.UserRole))
        return out

    def _sources_changed(self, *_args) -> None:
        if self._loading_sources:
            return
        setter = getattr(self._backend, "set_suwayomi_sources", None)
        if callable(setter):
            ticked = self.allowed_source_ids()
            setter(merge_allowed(self._stored_sources(), {src.id for src, _a in self._source_rows}, ticked))   # quick
            n, total = len(ticked), self.sources_list.count()
            self.sources_note.setText(f"{n} of {total} in use")
            self.downloads_changed.emit()

    def _stored_sources(self) -> list:
        """The allowed source ids as stored now - the setting both programs share (including the ones only the other
        program has installed, which this list does not show)."""
        from ..store.downloads import SuwayomiSettings

        try:
            return list(SuwayomiSettings(self._db).sources() or [])
        except Exception:  # noqa: BLE001 - nothing stored that could be lost
            return []

    def _move_source(self, step: int) -> None:
        row = self.sources_list.currentRow()
        to = row + step
        if row < 0 or not 0 <= to < self.sources_list.count():
            return
        self._loading_sources = True
        item = self.sources_list.takeItem(row)
        self.sources_list.insertItem(to, item)
        self.sources_list.setCurrentRow(to)
        self._loading_sources = False
        self._sources_changed()

    # The download budget (owner, 2026-10-09: "I set MangaList to use a maximum of 50 GB"): one cap over every download
    # client, so it has its own card under the sources. Its imports are local, to keep this lane's change inside the class.
    def _budget_card(self) -> QWidget:
        from PySide6.QtWidgets import QSpinBox

        from ..downloads.options import MAX_BUDGET_GB

        budget = card("true")
        bv = QVBoxLayout(budget)
        bv.setContentsMargins(20, 16, 20, 16)
        bv.setSpacing(10)
        self.budget_usage = label("", "muted")
        bv.addLayout(hbox(label("Download budget", "name"), label("Torrents (qBittorrent)", "muted"), None,
                          self.budget_usage, spacing=10))
        self.budget_spin = pad_spin(QSpinBox())
        self.budget_spin.setRange(0, MAX_BUDGET_GB)
        self.budget_spin.setSuffix(" GB")
        self.budget_spin.setSpecialValueText("no limit")            # 0 = no limit
        self.budget_spin.setMinimumWidth(120)
        self.budget_spin.setToolTip("0 = no limit. Default 50 GB.")
        bv.addLayout(hbox(label("Keep at most"), self.budget_spin, label("downloading or seeding"), None, spacing=8))
        self.budget_help = label(self._w(BUDGET_HELP), "muted", wrap=True)
        bv.addWidget(self.budget_help)
        return budget

    def _budget_cap(self) -> float:
        from ..downloads.options import get_budget_gb

        return get_budget_gb(self._db)

    def _show_budget_usage(self) -> None:
        """"using 12.3 GB of 50 GB" next to the setting (when the backend keeps a budget; one database read)."""
        getter = getattr(self._backend, "budget_status", None) if self._backend is not None else None
        text = ""
        if callable(getter):
            try:
                state = getter()
            except Exception:  # noqa: BLE001 - the usage is a courtesy; the setting works without it
                state = None
            if state is not None:
                text = state.usage_text()
                text = text[:1].upper() + text[1:]
                if state.queued:
                    text += f"; {len(state.queued)} queued"
        self.budget_usage.setText(text)
        self.budget_usage.setVisible(bool(text))

    def _budget_changed(self, value: int) -> None:
        if self._loading:
            return
        from ..downloads.options import set_budget_gb

        set_budget_gb(self._db, value)          # read at every send and every hand-over: nothing to reload
        self._show_budget_usage()

    def refresh(self) -> None:
        self._loading = True
        opts = load_nyaa_options(self._db)
        self.partial_check.setChecked(get_flag(self._db, KEY_PARTIAL_DOWNLOADS))
        self.partial_check.setEnabled(self._backend is not None)
        self.budget_spin.setValue(int(round(self._budget_cap())))
        self.budget_spin.setEnabled(self._backend is not None)
        self._show_budget_usage()
        self.on_check.setChecked(opts.enabled)
        self.english_check.setChecked(opts.english)
        self.raw_check.setChecked(opts.raw)
        self.novels_check.setChecked(opts.hide_light_novels)
        self.trusted_check.setChecked(opts.trusted_only)
        usable = self._backend is not None
        for box in (self.on_check, self.english_check, self.raw_check, self.novels_check, self.trusted_check):
            box.setEnabled(usable)
        self._loading = False
        self._update_badge(opts)

    def options(self) -> NyaaOptions:
        return NyaaOptions(enabled=self.on_check.isChecked(), english=self.english_check.isChecked(),
                           raw=self.raw_check.isChecked(), hide_light_novels=self.novels_check.isChecked(),
                           trusted_only=self.trusted_check.isChecked())

    def _update_badge(self, opts: NyaaOptions) -> None:
        if self._backend is None:
            text, kind = "Downloads off", "muted"
        elif not opts.enabled:
            text, kind = "Off", "muted"
        else:
            try:
                configured = bool(self._backend.load_settings().base_url)
            except BackendError:
                configured = False
            text, kind = ("Ready", "ok") if configured else ("Needs qBittorrent", "warn")
        self.nyaa_badge.setText(text)
        set_prop(self.nyaa_badge, "badge", kind)

    def _partial_changed(self, on: bool) -> None:
        if self._loading:
            return
        set_flag(self._db, KEY_PARTIAL_DOWNLOADS, on)       # read when a release is selected: nothing to reload

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        opts = self.options()
        if not opts.english and not opts.raw:               # a search of nothing: English stays
            self._loading = True
            self.english_check.setChecked(True)
            self._loading = False
            opts = replace(opts, english=True)
        save_nyaa_options(self._db, opts)
        self._update_badge(opts)
        self.downloads_changed.emit()

    def on_show(self) -> None:
        self._update_badge(load_nyaa_options(self._db))
        self._show_budget_usage()
        self._show_suwayomi_state()
        if self.sources_list.count() == 0:
            self.load_suwayomi_sources()

    def stop(self) -> None:
        self._retry_timer.stop()
        if self._sources_call is not None:
            self._sources_call.abandon()
            self._sources_call = None


class MatchingPage(SectionPage):
    def __init__(self, db, parent: Optional[QWidget] = None):
        super().__init__("Matching", MATCHING_LEAD, parent)
        self._db = db
        self.mu_check = checkbox("Look up new series on MangaUpdates automatically", get_flag(db, KEY_MU_AUTOSTART),
                                 tip="Starts the MangaUpdates lookup after each scan (the old \"Auto-start MU\")")
        self.mu_check.toggled.connect(lambda on: set_flag(self._db, KEY_MU_AUTOSTART, on))
        self.body.addWidget(self.mu_check)


class AutomationPage(SectionPage):
    def __init__(self, db, backend: Optional[DownloadsBackend], cache, parent: Optional[QWidget] = None,
                 env: Optional[Mapping[str, str]] = None):
        super().__init__("Automation", AUTOMATION_LEAD, parent)
        self._db = db
        self._backend = backend
        self._loading = True
        self._empty_call = None
        self.confirm_empty_all = self._ask_empty_all        # replaceable in tests (answers for the owner)

        self._env = env

        self.remove_check = checkbox("Remove Completed - delete the torrent and its downloaded copy once qBittorrent "
                                     "stops it at its seed goal")
        self.remove_check.toggled.connect(self._remove_toggled)
        self.body.addWidget(self.remove_check)
        self.scan_check = checkbox("Ask MangaPixer to rescan a library after filing into it",
                                   get_flag(db, KEY_SCAN_AFTER_FILING))
        self.scan_check.toggled.connect(lambda on: set_flag(self._db, KEY_SCAN_AFTER_FILING, on))
        self.body.addWidget(self.scan_check)
        self.scan_note = label(SCAN_FORBIDDEN_NOTE, wrap=True)
        self.scan_note.setProperty("tone", "warn")
        self.scan_note.setVisible(False)
        self.body.addWidget(self.scan_note)
        self.status_label = label("", wrap=True)
        self.status_label.setProperty("tone", "bad")
        self.body.addWidget(self.status_label)
        self._build_auto_downloads()
        self._build_replaced()
        self._cache = cache
        self.refresh()
        self._loading = False

    # Replaced chapters (upgrades): what happens to chapter files once a filed volume holds them.
    REPLACED_LEAD = ("When a volume you filed holds chapters you have as chapter files (an upgrade), those files are "
                     "no longer needed. Only chapters MangaPixer's volume list puts wholly in a filed volume count.")
    HOLDING_DAY_CHOICES = (7, 14, 30, 60, 90, 180, 365)
    HOLDING_HINT = ("Outside every library folder and MangaPixer library, on the same disk share as the library "
                    "(the container's /data). Files keep their folders there, so they can be restored.")

    # Automatic downloads (owner decisions 2026-10-10): the default mode; the per-series mode is in the series menus,
    # a library's in Library > Edit (2026-10-13).
    def _build_auto_downloads(self) -> None:
        box = card("true")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(20, 16, 20, 16)
        bv.setSpacing(6)
        bv.addWidget(label("Automatic downloads", "name"))
        bv.addWidget(label(AUTOMATIC_DOWNLOADS_LEAD, "lead", wrap=True))
        bv.addWidget(label("Default for new series", "h3"))
        self._auto_ok = has_automation(self._backend)
        self.default_mode_radios: Dict[str, QRadioButton] = {}
        self._default_group = QButtonGroup(self)
        for mode in MODES:
            radio = QRadioButton(MODE_LABELS[mode])
            radio.setAccessibleName(f"Default for new series: {MODE_LABELS[mode]}")
            radio.setEnabled(self._auto_ok)
            radio.toggled.connect(lambda on, m=mode: on and self._default_mode_chosen(m))
            self._default_group.addButton(radio)
            help_text = label(MODE_HELP[mode], "muted", wrap=True)
            help_text.setContentsMargins(26, 0, 0, 4)            # under the radio's text, not its circle
            bv.addWidget(radio)
            bv.addWidget(help_text)
            self.default_mode_radios[mode] = radio
        self.auto_note = label(AUTOMATIC_DOWNLOADS_NOTE if self._auto_ok else AUTOMATIC_DOWNLOADS_OFF, "muted",
                               wrap=True)
        bv.addWidget(self.auto_note)
        self.auto_status = label("", wrap=True)
        self.auto_status.setVisible(False)
        bv.addWidget(self.auto_status)
        self.body.addWidget(box)

    def _show_default_mode(self) -> None:
        if not self._auto_ok:
            for radio in self.default_mode_radios.values():
                radio.setChecked(False)
            return
        try:
            mode = self._backend.default_mode()
        except Exception as exc:  # noqa: BLE001 - shown, nothing changed
            self._say_auto(f"Could not read the default: {describe_error(exc)}", "bad")
            return
        radio = self.default_mode_radios.get(mode)
        if radio is not None:
            radio.setChecked(True)

    def _say_auto(self, text: str, tone: str = "") -> None:
        self.auto_status.setText(text)
        self.auto_status.setVisible(bool(text))
        set_prop(self.auto_status, "tone", tone if text else "")

    def _default_mode_chosen(self, mode: str) -> None:
        if self._loading or not self._auto_ok:
            return
        try:
            if self._backend.default_mode() == mode:
                return
            self._backend.set_default_mode(mode)
        except Exception as exc:  # noqa: BLE001 - shown; the radios go back to what is stored
            self._say_auto(f"Could not change the default: {describe_error(exc)}", "bad")
            self._loading = True
            self._show_default_mode()
            self._loading = False
            return
        self._say_auto(f"Saved: series you have not set are now {MODE_LABELS[mode]}.", "ok")
        self.downloads_changed.emit()

    def _build_replaced(self) -> None:
        box = card("true")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(20, 16, 20, 16)
        bv.setSpacing(8)
        bv.addWidget(label("Replaced chapters", "name"))
        bv.addWidget(label(self.REPLACED_LEAD, "lead", wrap=True))
        self.hold_radio = QRadioButton("Move them to a holding folder - restorable, done after each filing")
        self.delete_radio = QRadioButton("Delete them after I confirm the list of files - nothing is deleted before")
        self._mode_group = QButtonGroup(self)
        for radio in (self.hold_radio, self.delete_radio):
            self._mode_group.addButton(radio)
            bv.addWidget(radio)
        self.holding_edit = QLineEdit()
        self.holding_edit.setAccessibleName("Holding folder")
        self.holding_edit.setPlaceholderText(upgrades.DEFAULT_HOLDING_FOLDER)
        self.days_combo = QComboBox()
        self.days_combo.setAccessibleName("Empty the holding folder after")
        self.days_combo.setMinimumWidth(140)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        grid.addWidget(label("Holding folder"), 0, 0)
        grid.addWidget(self.holding_edit, 0, 1)
        grid.addWidget(label("Empty it after"), 1, 0)
        grid.addLayout(hbox(self.days_combo, None), 1, 1)
        grid.setColumnStretch(1, 1)
        bv.addLayout(grid)
        self.holding_hint = label(self.HOLDING_HINT, "muted", wrap=True)
        bv.addWidget(self.holding_hint)
        # Empty it early (owner, 2026-10-09) - only batches whose volumes are still in the library.
        self.held_label = label("", "muted")
        self.btn_empty_holding = button("Empty the holding folder now...")
        self.btn_empty_holding.setToolTip("Delete what the holding folder keeps now, instead of after the period above - "
                                          "only where the volumes that replaced the chapters are still in the library")
        self.btn_empty_holding.clicked.connect(self.empty_holding_now)
        bv.addLayout(hbox(self.btn_empty_holding, self.held_label, None))
        self.replaced_status = label("", wrap=True)
        bv.addWidget(self.replaced_status)
        self.body.addWidget(box)
        self.hold_radio.toggled.connect(self._replaced_mode_changed)
        self.holding_edit.editingFinished.connect(self._holding_folder_changed)
        self.days_combo.currentIndexChanged.connect(self._holding_days_changed)

    def _refresh_replaced(self) -> None:
        settings = upgrades.load_settings(self._db)
        self.hold_radio.setChecked(settings.mode == upgrades.MODE_HOLDING)
        self.delete_radio.setChecked(settings.mode == upgrades.MODE_DELETE)
        self.holding_edit.setText(settings.holding_folder)
        self.days_combo.clear()
        for days in sorted(set(self.HOLDING_DAY_CHOICES) | {settings.holding_days}):
            self.days_combo.addItem(f"{days} days" if days != 1 else "1 day", days)
        self.days_combo.setCurrentIndex(self.days_combo.findData(settings.holding_days))
        self._show_holding_state(settings.mode)
        self._show_held()

    def _held(self):
        from ..store.replacements import ReplacementStore

        try:
            return ReplacementStore(self._db).with_status("held")
        except Exception:  # noqa: BLE001 - a count only
            return []

    def _show_held(self) -> None:
        held = self._held()
        files = sum(len(b.files) for b in held)          # the space first (owner, 2026-10-10)
        self.held_label.setText(f"{held_size_text(held)} held ({len(held)} batch{'es' if len(held) != 1 else ''}, "
                                f"{files} file{'s' if files != 1 else ''})" if held else "Nothing is held")
        self.btn_empty_holding.setEnabled(bool(held) and self._empty_call is None)

    def _ask_empty_all(self, held) -> bool:
        files = sum(len(b.files) for b in held)
        box = QMessageBox(QMessageBox.Icon.Question, "Empty the holding folder now?",
                          f"Delete the {files} replaced chapter file{'s' if files != 1 else ''} the holding folder keeps "
                          "now? They cannot be restored afterwards.\n\nA batch is only emptied while the volumes that "
                          "replaced it are still in the library; any other stays held.", parent=self)
        yes = box.addButton("Empty now", QMessageBox.ButtonRole.DestructiveRole)
        no = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(no)
        box.exec()
        return box.clickedButton() is yes

    def empty_holding_now(self) -> bool:
        """Empty every held batch whose volumes are still in the library (after a yes; off the UI thread)."""
        held = self._held()
        if not held or self._empty_call is not None or not self.confirm_empty_all(held):
            return False
        self._say_replaced("Emptying the holding folder...", "")
        self.btn_empty_holding.setEnabled(False)
        db = self._db
        self._empty_call = start_call(lambda: upgrades.empty_all_now(db), self._emptied,
                                      lambda message: self._say_replaced(f"Could not empty it: {message}", "bad"),
                                      self._empty_finished)
        return True

    def _emptied(self, result) -> None:
        emptied, refused = result
        text = f"Emptied {len(emptied)} batch{'es' if len(emptied) != 1 else ''}."
        if refused:
            text += " Kept: " + "; ".join(why for _id, why in refused[:3]) + (" ..." if len(refused) > 3 else "")
        self._say_replaced(text, "bad" if refused else "")

    def _empty_finished(self) -> None:
        self._empty_call = None
        self._show_held()

    def _show_holding_state(self, mode: str) -> None:
        holding = mode == upgrades.MODE_HOLDING
        for widget in (self.holding_edit, self.days_combo, self.holding_hint):
            widget.setEnabled(holding)
        problem = upgrades.holding_problem(self._db, self.holding_edit.text()) if holding else None
        self._say_replaced(f"The holding folder cannot be used: {problem}. Nothing is moved until it is fixed."
                           if problem else "", "bad")

    def _say_replaced(self, text: str, tone: str) -> None:
        self.replaced_status.setText(text)
        set_prop(self.replaced_status, "tone", tone if text else "")

    def _replaced_mode_changed(self, *_args) -> None:
        if self._loading:
            return
        mode = upgrades.MODE_HOLDING if self.hold_radio.isChecked() else upgrades.MODE_DELETE
        upgrades.set_mode(self._db, mode)
        self._show_holding_state(mode)
        self.downloads_changed.emit()

    def _holding_folder_changed(self) -> None:
        if self._loading:
            return
        text = self.holding_edit.text().strip()
        if text == upgrades.load_settings(self._db).holding_folder:
            return
        try:
            upgrades.set_holding_folder(self._db, text)
        except ValueError as exc:
            self._loading = True
            self.holding_edit.setText(upgrades.load_settings(self._db).holding_folder)
            self._loading = False
            self._say_replaced(f"Not changed: {exc}.", "bad")
            return
        self._say_replaced("Holding folder saved.", "ok")
        self.downloads_changed.emit()

    def _holding_days_changed(self, _index: int) -> None:
        days = self.days_combo.currentData()
        if self._loading or not isinstance(days, int):
            return
        upgrades.set_holding_days(self._db, days)

    def refresh(self) -> None:
        self._loading = True
        backend = self._backend
        self.remove_check.setEnabled(backend is not None)
        if backend is None:
            self.remove_check.setChecked(False)
            self.remove_check.setToolTip("Downloads are switched off in this installation")
        else:
            try:
                self.remove_check.setChecked(backend.load_settings().remove_completed)
            except BackendError:
                self.remove_check.setChecked(False)
        self.scan_note.setVisible(bool(self._cache is not None and scan_forbidden(self._cache)))
        self._refresh_replaced()
        self._show_default_mode()
        self._loading = False

    def on_show(self) -> None:
        self.refresh()

    def _remove_toggled(self, on: bool) -> None:
        if self._loading or self._backend is None:
            return
        backend = self._backend
        setter = getattr(backend, "set_remove_completed", None)
        try:
            if setter is not None:
                setter(on)
            else:
                backend.save_settings(replace(backend.load_settings(), remove_completed=on), None)
        except BackendError as exc:
            self._loading = True
            self.remove_check.setChecked(not on)
            self._loading = False
            self.status_label.setText(f"Could not change Remove Completed: {exc}")
            return
        self.status_label.setText("")
        self.downloads_changed.emit()


LOG_LEVEL_HELP = {
    "error": "Only failures.",
    "warning": "Failures and problems MangaList got past.",
    "info": "What MangaList does, one line per action. The usual choice.",
    "debug": "Everything, for tracking down a fault. The files grow quickly.",
}


class LoggingPage(SectionPage):
    """The log viewer (View logs), the log level (one for the app and the container's runner: both read the same
    settings), optional levels per area, the size of the files, and the log folder. Every change is stored and
    applied at once (no Save)."""

    def __init__(self, db, parent: Optional[QWidget] = None, env: Optional[Mapping[str, str]] = None,
                 open_folder: Optional[Callable[[str], object]] = None,
                 copy_text: Optional[Callable[[str], object]] = None,
                 view_logs: Optional[Callable[[Optional[QWidget]], object]] = None):
        super().__init__("Logging", LOGGING_LEAD, parent)
        self._db = db
        self._env = env
        self._loading = True
        self._open_folder = open_folder or open_folder_default
        self._copy_text = copy_text or (lambda text: QApplication.clipboard().setText(text))
        self._view_logs = view_logs
        self.viewer = None

        viewer = card("true")
        vv = QVBoxLayout(viewer)
        vv.setContentsMargins(20, 16, 20, 16)
        vv.setSpacing(8)
        vv.addWidget(label("Log viewer", "name"))
        vv.addWidget(label("Read the app's log and the scheduled jobs' log here: newest lines at the bottom, "
                           "following live, with filters for the level, the part of the app and a search. "
                           "Nothing in the files is changed.", "muted", wrap=True))
        self.btn_view = button("View logs", primary=True)
        self.btn_view.setAccessibleName("View logs")
        vv.addLayout(hbox(self.btn_view, None))
        self.body.addWidget(viewer)

        box = card("true")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(20, 16, 20, 16)
        bv.setSpacing(10)
        self.level_combo = QComboBox()
        self.level_combo.setAccessibleName("Log level")
        for key, text in log_config.LEVEL_LABELS.items():
            self.level_combo.addItem(text, key)
        self.level_help = label("", "muted", wrap=True)
        bv.addLayout(hbox(label("Log level", "name"), self.level_combo, None, spacing=12))
        bv.addWidget(self.level_help)
        bv.addWidget(label("The same level is used by the desktop app and by the container's background runner "
                           "(they write separate files). The runner picks a change up within a minute.", "muted",
                           wrap=True))
        self.body.addWidget(box)

        areas = card("true")
        av = QVBoxLayout(areas)
        av.setContentsMargins(20, 16, 20, 16)
        av.setSpacing(8)
        av.addWidget(label("Levels for one part of the app", "name"))
        av.addWidget(label("Optional. Turn one part up to Debug while you look into a problem, or down to Error "
                           "to quiet it, without changing the rest.", "muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.area_combos: Dict[str, QComboBox] = {}
        for row, (key, text, _prefixes) in enumerate(log_config.AREAS):
            combo = QComboBox()
            combo.setAccessibleName(f"Log level for {text}")
            combo.addItem("Same as above", "")
            for level, name in log_config.LEVEL_LABELS.items():
                combo.addItem(name, level)
            self.area_combos[key] = combo
            grid.addWidget(label(text), row, 0)
            grid.addWidget(combo, row, 1)
        grid.setColumnStretch(2, 1)
        av.addLayout(grid)
        self.body.addWidget(areas)

        files = card("true")
        fv = QVBoxLayout(files)
        fv.setContentsMargins(20, 16, 20, 16)
        fv.setSpacing(8)
        fv.addWidget(label("Log files", "name"))
        self.size_spin = pad_spin(QSpinBox())
        self.size_spin.setAccessibleName("Size of a log file")
        self.size_spin.setRange(*log_config.MAX_MB_RANGE)
        self.size_spin.setSuffix(" MB")
        self.keep_spin = pad_spin(QSpinBox())
        self.keep_spin.setAccessibleName("Old log files kept")
        self.keep_spin.setRange(*log_config.BACKUPS_RANGE)
        fgrid = QGridLayout()
        fgrid.setHorizontalSpacing(16)
        fgrid.setVerticalSpacing(6)
        fgrid.addWidget(label("A file is closed at"), 0, 0)
        fgrid.addLayout(hbox(self.size_spin, None), 0, 1)
        fgrid.addWidget(label("Old files kept"), 1, 0)
        fgrid.addLayout(hbox(self.keep_spin, None), 1, 1)
        fgrid.setColumnStretch(1, 1)
        fv.addLayout(fgrid)
        self.space_label = label("", "muted", wrap=True)
        fv.addWidget(self.space_label)
        self.folder_label = label(str(paths.log_dir()), "mono", wrap=True, selectable=True)
        fv.addWidget(self.folder_label)
        self.btn_open = button("Open the log folder")
        self.btn_copy = button("Copy the log folder path")
        fv.addLayout(hbox(self.btn_open, self.btn_copy, None))
        self.folder_status = label("", wrap=True)
        fv.addWidget(self.folder_status)
        self.body.addWidget(files)

        self.refresh()
        self.level_combo.currentIndexChanged.connect(self._changed)
        for combo in self.area_combos.values():
            combo.currentIndexChanged.connect(self._changed)
        self.size_spin.valueChanged.connect(self._changed)
        self.keep_spin.valueChanged.connect(self._changed)
        self.btn_open.clicked.connect(self.open_log_folder)
        self.btn_copy.clicked.connect(self.copy_log_folder)
        self.btn_view.clicked.connect(self.view_logs)
        self._loading = False

    def refresh(self) -> None:
        was, self._loading = self._loading, True
        s = log_config.load(self._db, self._env)
        self.level_combo.setCurrentIndex(self.level_combo.findData(s.level))
        for key, combo in self.area_combos.items():
            combo.setCurrentIndex(combo.findData(s.areas.get(key, "")))
        self.size_spin.setValue(s.max_mb)
        self.keep_spin.setValue(s.backups)
        self._show_help(s)
        self._loading = was

    def settings(self) -> "log_config.LogSettings":
        return log_config.LogSettings(
            level=self.level_combo.currentData(),
            areas={key: combo.currentData() for key, combo in self.area_combos.items() if combo.currentData()},
            max_mb=self.size_spin.value(), backups=self.keep_spin.value())

    def _show_help(self, s: "log_config.LogSettings") -> None:
        self.level_help.setText(LOG_LEVEL_HELP[s.level])
        total = s.max_mb * (s.backups + 1)
        self.space_label.setText(f"Up to {total} MB on disk for each log (the file being written, {s.max_mb} MB, plus "
                                 f"{s.backups} old file{'s' if s.backups != 1 else ''}); the oldest is deleted first.")

    def _changed(self, *_args) -> None:
        if self._loading:
            return
        s = log_config.save(self._db, self.settings())          # stored, then in force at once in this window
        log_config.apply(s)
        self._show_help(s)

    def open_log_folder(self) -> bool:
        folder = paths.log_dir()
        try:
            folder.mkdir(parents=True, exist_ok=True)           # nothing is written before the first message
            opened = self._open_folder(str(folder))
        except OSError as exc:
            opened, why = False, f" ({exc})"
        else:
            why = ""
        if opened is False:
            self._say_folder(f"Could not open the folder{why}. Its path is above; copy it instead.", "bad")
            return False
        self._say_folder("", "")
        return True

    def copy_log_folder(self) -> None:
        self._copy_text(str(paths.log_dir()))
        self._say_folder("Path copied.", "ok")

    def _say_folder(self, text: str, tone: str) -> None:
        self.folder_status.setText(text)
        set_prop(self.folder_status, "tone", tone if text else "")

    def on_show(self) -> None:
        self.refresh()

    def view_logs(self):
        """Open the log viewer (one window; a second click brings it to the front). It belongs to the Settings
        window while that is open (Settings is modal), and moves to the main window when Settings closes."""
        if self._view_logs is not None:
            self.viewer = self._view_logs(self.window())
        else:
            from .log_viewer import open_log_viewer

            self.viewer = open_log_viewer(self.window())
        return self.viewer

    def stop(self) -> None:
        viewer, self.viewer = self.viewer, None
        if viewer is None or not hasattr(viewer, "move_to"):
            return
        try:
            from shiboken6 import isValid
        except ImportError:  # pragma: no cover - shiboken ships with PySide6
            isValid = bool  # noqa: N806
        if isValid(viewer) and viewer.parentWidget() is self.window():
            viewer.move_to(self.window().parentWidget())        # outlives Settings, owned by the main window
