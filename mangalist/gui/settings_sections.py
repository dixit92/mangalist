"""Settings > Download sources, Matching, Automation and Logging.

**Download sources** - where releases come from. nyaa (volumes; needs qBittorrent) with the options the search already
supports (English / raw, hide light novels, only trusted uploaders) and two shown fixed because that is how the search
always works (Digital first, no 0-seeder releases); Suwayomi sources (chapters; needs Suwayomi): the sources Suwayomi has
installed, read from Suwayomi when the section is shown - which MangaList may use (ticked) and in what order it tries them
(MangaDex first by default); stored by Suwayomi's source id. **Matching** - where series information comes from. **Automation** - the schedules (editable,
stored in the database; the container's variables only seed them), Remove Completed, "ask MangaPixer to rescan after
filing". **Logging** - the level of the log files (and optionally of one area), their size, and the log folder.

Every switch is stored as soon as it is changed (there is no Save here), in the library database's settings.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Dict, Mapping, Optional

from PySide6.QtCore import QTime, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTimeEdit,
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
from .background import start_call
from .download_rules import (
    CHOICE_DAILY,
    CHOICE_WEEKLY,
    DAY_NAMES,
    ScheduleChoice,
    choice_of,
    held_size_text,
    reset_schedule_text,
    save_schedule_text,
    schedule_choices,
    schedule_entries,
)
from .download_style import set_prop
from .download_widgets import button, card, checkbox, hbox, label, pill
from .spin_arrows import pad_spin
from .downloads_backend import BackendError, DownloadsBackend
from .settings_common import SectionPage, placeholder
from .settings_services import SCAN_FORBIDDEN_NOTE, scan_forbidden
from ..headless.settings import SOURCE_STORED
from .shell import SECTION_SERVICES

SECTION_LOGGING = "logging"

SOURCES_LEAD = "Where releases come from. A source works only when the service it needs is connected."
MATCHING_LEAD = "Where series information comes from. Not download sources."
AUTOMATION_LEAD = "What runs on its own in the container."
LOGGING_LEAD = "What MangaList writes to its log files, and where they are."
SCHEDULE_NOTE = ("These times are used by MangaList's background runner in the Docker / Unraid container, in the "
                 "container's time zone; the desktop app on its own runs nothing on a timer. A change reaches the "
                 "runner within a minute - no restart.")
SUWAYOMI_EXPLAIN = ("The sources installed in Suwayomi (from their extensions). Tick the ones MangaList may use, in "
                    "the order it tries them: MangaDex first - it is found by the MangaDex id MangaPixer links; the "
                    "others by title, and you confirm the match once per series. The scanlation group is chosen per "
                    "series in the Download tab.")
AUTOMATIC_DOWNLOADS = "Automatic downloads - off / notify / automatic, per series and a default (next phase)."
FIXED_DIGITAL = "Always on: the ranking puts Digital releases first."
FIXED_SEEDERS = "Always on: releases nobody is seeding are never shown."


class SourcesPage(SectionPage):
    def __init__(self, db, backend: Optional[DownloadsBackend], parent: Optional[QWidget] = None):
        super().__init__("Download sources", SOURCES_LEAD, parent)
        self._db = db
        self._backend = backend
        self._loading = True

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
        sv.addLayout(hbox(label("Suwayomi sources", "name"), label("Chapters · needs Suwayomi", "muted"),
                          self.suwayomi_badge, None, self.btn_sources_reset, self.btn_sources_reload, self.btn_suwayomi,
                          spacing=10))
        sv.addWidget(label(SUWAYOMI_EXPLAIN, "lead", wrap=True))
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
        self.suwayomi_rows = suwayomi
        self._sources_call = None
        self._loading_sources = False
        self._show_suwayomi_state()
        return suwayomi

    def _suwayomi_ready(self) -> bool:
        ready = getattr(self._backend, "suwayomi_ready", None) if self._backend is not None else None
        try:
            return bool(ready()) if callable(ready) else False
        except Exception:  # noqa: BLE001
            return False

    def _show_suwayomi_state(self) -> None:
        from .download_style import set_prop

        ready = self._suwayomi_ready()
        self.suwayomi_badge.setText("Ready" if ready else "Suwayomi not set up")
        set_prop(self.suwayomi_badge, "badge", "ok" if ready else "warn")
        self.btn_suwayomi.setVisible(not ready)
        for w in (self.sources_list, self.btn_up, self.btn_down, self.btn_sources_reload, self.btn_sources_reset):
            w.setEnabled(ready)
        if not ready:
            self.sources_note.setText("Connect Suwayomi first (Settings > Connected services).")

    def load_suwayomi_sources(self) -> bool:
        """Read the installed sources from Suwayomi (off the UI thread)."""
        loader = getattr(self._backend, "suwayomi_sources", None) if self._backend is not None else None
        if not callable(loader) or not self._suwayomi_ready() or self._sources_call is not None:
            return False
        self.sources_note.setText("Reading the sources from Suwayomi...")

        def done() -> None:
            self._sources_call = None

        self._sources_call = start_call(loader, self.show_sources,
                                        lambda message: self.sources_note.setText(f"Could not read them: {message}"),
                                        done)
        return True

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
                                  "Suwayomi has no sources yet: install extensions in Suwayomi (MangaDex first).")

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
            setter(self.allowed_source_ids())           # quick: one setting
            n, total = len(self.allowed_source_ids()), self.sources_list.count()
            self.sources_note.setText(f"{n} of {total} in use")
            self.downloads_changed.emit()

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
        bv.addWidget(label("Counts every download MangaList has sent and not yet removed: downloading, waiting to be "
                           "filed, and seeding (a partial download counts only its selected files). A send that would go "
                           "over it waits in a queue and goes to qBittorrent by itself once Remove Completed has made "
                           "room. Lowering it removes nothing; new sends then wait. Chapter downloads (Suwayomi) do not "
                           "count: they do not seed.", "muted", wrap=True))
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
        self._build_schedules()

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
        self._build_replaced()
        self.body.addWidget(placeholder(AUTOMATIC_DOWNLOADS))
        self._cache = cache
        self.refresh()
        self._loading = False

    # Replaced chapters (upgrades): what happens to chapter files once a filed volume holds them.
    REPLACED_LEAD = ("When a volume you filed holds chapters you have as chapter files (an upgrade), those files are "
                     "no longer needed. Only chapters MangaPixer's volume list puts wholly in a filed volume count.")
    HOLDING_DAY_CHOICES = (7, 14, 30, 60, 90, 180, 365)
    HOLDING_HINT = ("Outside every library folder and MangaPixer library, on the same disk share as the library "
                    "(the container's /data). Files keep their folders there, so they can be restored.")

    # Schedules (owner, 2026-10-09: "This should be configurable by the user"): stored in the database, so the runner in
    # the container re-reads them; the container's variables only seed them.
    def _build_schedules(self) -> None:
        """One row per schedule: a choice (Weekly, Daily, Every 12 hours, Every 6 hours, Off - owner, 2026-10-10), the
        day and the time where the choice needs them, then the reading in words and where it comes from."""
        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 200)
        grid.setColumnMinimumWidth(1, 160 + 130 + 80 + 16)      # Weekly's three controls, so every row lines up
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self.schedule_modes: Dict[str, QComboBox] = {}
        self.schedule_days: Dict[str, QComboBox] = {}
        self.schedule_times: Dict[str, QTimeEdit] = {}
        self.schedule_reading: Dict[str, QLabel] = {}
        self.schedule_reset: Dict[str, QPushButton] = {}
        self.schedule_source: Dict[str, QLabel] = {}
        for row, entry in enumerate(schedule_entries(self._db, self._env)):
            mode = QComboBox()
            mode.setAccessibleName(entry.label)
            mode.setFixedWidth(160)                             # the same width whether the day / time show or not
            day = QComboBox()
            day.setAccessibleName(f"{entry.label}: day")
            day.setFixedWidth(130)
            for i, name in enumerate(DAY_NAMES):
                day.addItem(name, i)
            at = QTimeEdit()
            at.setDisplayFormat("HH:mm")
            at.setAccessibleName(f"{entry.label}: time")
            reset = button("Use the container's value", link=True,
                           tip="Forget the time set here; the container's own setting (or the default) applies again")
            source = label("", "muted", wrap=True)
            reading = label("", "mono", wrap=True)              # wraps in a narrow window instead of widening it
            grid.addWidget(label(entry.label), row, 0)
            grid.addLayout(hbox(mode, day, at, None, spacing=8), row, 1)
            grid.addLayout(hbox(reading, source, reset, None, spacing=12), row, 2)
            self.schedule_modes[entry.job] = mode
            self.schedule_days[entry.job] = day
            self.schedule_times[entry.job] = at
            self.schedule_reading[entry.job] = reading
            self.schedule_source[entry.job] = source
            self.schedule_reset[entry.job] = reset
            mode.currentIndexChanged.connect(lambda _i, job=entry.job: self._schedule_edited(job))
            day.currentIndexChanged.connect(lambda _i, job=entry.job: self._schedule_edited(job))
            at.editingFinished.connect(lambda job=entry.job: self._schedule_edited(job))
            reset.clicked.connect(lambda _=False, job=entry.job: self._schedule_reset(job))
        grid.setColumnStretch(2, 1)
        self.body.addLayout(grid)
        self.schedule_status = label("", wrap=True)
        self.body.addWidget(self.schedule_status)
        self.body.addWidget(label(SCHEDULE_NOTE, "muted", wrap=True))
        self._show_schedules()

    def _show_schedules(self) -> None:
        was, self._loading = self._loading, True               # setting the controls is not an edit
        try:
            for entry in schedule_entries(self._db, self._env):
                job, now = entry.job, choice_of(entry.edit_text)
                mode, day, at = self.schedule_modes[job], self.schedule_days[job], self.schedule_times[job]
                mode.clear()
                for value, text in schedule_choices(job, entry.edit_text):
                    mode.addItem(text, value)
                mode.setCurrentIndex(max(0, mode.findData(now.choice)))
                day.setCurrentIndex(now.weekday)
                at.setTime(QTime(now.hour, now.minute))
                self._show_day_time(job)
                self.schedule_reading[job].setText(entry.when)
                stored = entry.source == SOURCE_STORED
                self.schedule_source[job].setText("set here" if stored else "from the container")
                self.schedule_reset[job].setVisible(stored)
                set_prop(self.schedule_reading[job], "tone", "" if entry.valid else "bad")
        finally:
            self._loading = was

    def _show_day_time(self, job: str) -> None:
        choice = self.schedule_modes[job].currentData()
        self.schedule_days[job].setVisible(choice == CHOICE_WEEKLY)
        self.schedule_times[job].setVisible(choice in (CHOICE_WEEKLY, CHOICE_DAILY))

    def schedule_choice(self, job: str) -> ScheduleChoice:
        """What *job*'s controls say now."""
        at = self.schedule_times[job].time()
        return ScheduleChoice(self.schedule_modes[job].currentData() or "", self.schedule_days[job].currentData() or 0,
                              at.hour(), at.minute())

    def _say_schedule(self, text: str, tone: str = "") -> None:
        self.schedule_status.setText(text)
        set_prop(self.schedule_status, "tone", tone if text else "")

    def _schedule_edited(self, job: str) -> None:
        if self._loading:
            return
        self._show_day_time(job)
        current = next(e for e in schedule_entries(self._db, self._env) if e.job == job)
        text = self.schedule_choice(job).text()
        if text == current.edit_text or (not current.valid and text == choice_of(current.edit_text).choice):
            return                                              # nothing changed (or the bad value still chosen)
        try:
            save_schedule_text(self._db, job, text)
        except ValueError as exc:
            self._say_schedule(f"{current.label}: {exc} Nothing was changed.", "bad")
            self._show_schedules()
            return
        self._show_schedules()
        self._say_schedule(f"{current.label}: saved. The runner uses it within a minute.", "ok")

    def _schedule_reset(self, job: str) -> None:
        reset_schedule_text(self._db, job)
        self._show_schedules()
        self._say_schedule("Back to the container's value.", "ok")

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
        self._show_schedules()
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
    """The log level (one for the app and the container's runner: both read the same settings), optional levels per
    area, the size of the files, and the log folder. Every change is stored and applied at once (no Save)."""

    def __init__(self, db, parent: Optional[QWidget] = None, env: Optional[Mapping[str, str]] = None,
                 open_folder: Optional[Callable[[str], object]] = None,
                 copy_text: Optional[Callable[[str], object]] = None):
        super().__init__("Logging", LOGGING_LEAD, parent)
        self._db = db
        self._env = env
        self._loading = True
        self._open_folder = open_folder or (lambda folder: QDesktopServices.openUrl(QUrl.fromLocalFile(folder)))
        self._copy_text = copy_text or (lambda text: QApplication.clipboard().setText(text))

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
