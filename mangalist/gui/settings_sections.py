"""Settings > Download sources, Matching and Automation.

**Download sources** - where releases come from. nyaa (volumes; needs qBittorrent) with the options the search already
supports (English / raw, hide light novels, only trusted uploaders) and two shown fixed because that is how the search
always works (Digital first, no 0-seeder releases); Suwayomi sources (chapters; needs Suwayomi) greyed out until
Suwayomi is connected. **Matching** - where series information comes from. **Automation** - the schedules (read-only:
they come from the container's environment), Remove Completed, "ask MangaPixer to rescan after filing".

Every switch is stored as soon as it is changed (there is no Save here), in the library database's settings.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Mapping, Optional

from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QGraphicsOpacityEffect,
    QGridLayout,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from .. import upgrades

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
from .download_rules import schedule_rows
from .download_style import set_prop
from .download_widgets import button, card, checkbox, hbox, label, pill
from .downloads_backend import BackendError, DownloadsBackend
from .settings_common import SectionPage, placeholder
from .settings_services import SCAN_FORBIDDEN_NOTE, scan_forbidden
from .shell import SECTION_SERVICES

SOURCES_LEAD = "Where releases come from. A source works only when the service it needs is connected."
MATCHING_LEAD = "Where series information comes from. Not download sources."
AUTOMATION_LEAD = "What runs on its own in the container."
SUWAYOMI_EXPLAIN = ("Once connected: the sources Suwayomi offers (MangaDex, official English sites, ...) in the order "
                    "MangaList tries them, which to never use, and the preferred scanlation groups - for all series, "
                    "or per series.")
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
        nv.setContentsMargins(16, 14, 16, 14)
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

        suwayomi = card("quiet")
        sv = QVBoxLayout(suwayomi)
        sv.setContentsMargins(16, 14, 16, 14)
        sv.setSpacing(10)
        self.btn_suwayomi = button("Set up Suwayomi")
        self.btn_suwayomi.clicked.connect(lambda: self.section_requested.emit(SECTION_SERVICES))
        sv.addLayout(hbox(label("Suwayomi sources", "name"), label("Chapters · needs Suwayomi", "muted"),
                          pill("Suwayomi not set up", "warn"), None, self.btn_suwayomi, spacing=10))
        sv.addWidget(label(SUWAYOMI_EXPLAIN, "lead", wrap=True))
        for number, name, note in (("1", "MangaDex", "preferred groups: any"),
                                   ("2", "[Official English source]", "when it has the chapter")):
            row = hbox(label(number, "mono"), label(name), label(note, "muted"), None, spacing=10)
            holder = QWidget()
            holder.setLayout(row)
            fade = QGraphicsOpacityEffect(holder)          # the mockup's greyed-out example rows
            fade.setOpacity(0.55)
            holder.setGraphicsEffect(fade)
            sv.addWidget(holder)
        self.suwayomi_rows = suwayomi
        self.body.addWidget(suwayomi)

        self.refresh()
        for box in (self.on_check, self.english_check, self.raw_check, self.novels_check, self.trusted_check):
            box.toggled.connect(self._changed)
        self.partial_check.toggled.connect(self._partial_changed)

    def refresh(self) -> None:
        self._loading = True
        opts = load_nyaa_options(self._db)
        self.partial_check.setChecked(get_flag(self._db, KEY_PARTIAL_DOWNLOADS))
        self.partial_check.setEnabled(self._backend is not None)
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

        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 260)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)
        self.schedule_labels = []
        for row, (what, when, from_env) in enumerate(schedule_rows(env)):
            grid.addWidget(label(what), row, 0)
            text = label(when, "mono")
            text.setToolTip("Set by the container's environment" if from_env else "The default; set by the container's "
                            "environment variable")
            self.schedule_labels.append((what, text))
            grid.addWidget(text, row, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)
        self.body.addWidget(label("These times come from the container's settings and are changed there.", "muted"))

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

    def _build_replaced(self) -> None:
        box = card("true")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(16, 14, 16, 14)
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
