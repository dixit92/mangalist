"""Settings > Appearance: Light, Dark or Follow the system (owner, 2026-10-13: "we need a dark mode option. Switching
from animefetch to MangaList to MangaPixer is disorienting.").

The choice is stored in the library database's settings (:data:`~mangalist.gui.palette.KEY_APPEARANCE`) as soon as it
is picked, and applied at once to every open window (:func:`~mangalist.gui.theme.set_appearance`). Light is today's
look and the default until the owner chooses.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from PySide6.QtWidgets import QApplication, QButtonGroup, QRadioButton, QVBoxLayout, QWidget

from . import palette, theme
from .download_widgets import card, label
from .settings_common import SectionPage

SECTION_APPEARANCE = "appearance"

APPEARANCE_LEAD = "How MangaList looks. A change shows at once in every open window."
APPEARANCE_HELP = {
    palette.APPEARANCE_LIGHT: "Light grey and white panels - the look MangaList has always had.",
    palette.APPEARANCE_DARK: "Dark panels in MangaPixer's dark colours, so moving between the apps is not a jump.",
    palette.APPEARANCE_SYSTEM: ("Light or Dark as your computer is set, and it follows when that changes. Where the "
                                "system says nothing - the Docker container's web page - it is Light, unless the "
                                "container is started with DARK_MODE=1."),
}
CONTAINER_NOTE = ("In the Docker container the window frames of dialogs, the file manager and the web page's own bar "
                  "come from the container, not from MangaList: they turn dark when the container is started with "
                  "the variable DARK_MODE=1 (a container restart).")


class AppearancePage(SectionPage):
    """Three choices as radio buttons; picking one stores it and switches the look (no Save)."""

    def __init__(self, db, parent: Optional[QWidget] = None,
                 apply: Optional[Callable[[str], object]] = None):
        super().__init__("Appearance", APPEARANCE_LEAD, parent)
        self._db = db
        self._apply = apply or (lambda choice: theme.set_appearance(QApplication.instance(), choice))
        self._loading = True

        box = card("true")
        bv = QVBoxLayout(box)
        bv.setContentsMargins(20, 16, 20, 16)
        bv.setSpacing(6)
        bv.addWidget(label("Appearance", "name"))
        self.group = QButtonGroup(self)
        self.radios: Dict[str, QRadioButton] = {}
        for choice in palette.APPEARANCES:
            radio = QRadioButton(palette.APPEARANCE_LABELS[choice])
            radio.setAccessibleName(f"Appearance: {palette.APPEARANCE_LABELS[choice]}")
            self.group.addButton(radio)
            self.radios[choice] = radio
            bv.addWidget(radio)
            help_label = label(APPEARANCE_HELP[choice], "muted", wrap=True)
            help_label.setContentsMargins(24, 0, 0, 6)
            bv.addWidget(help_label)
        self.body.addWidget(box)
        self.body.addWidget(label(CONTAINER_NOTE, "muted", wrap=True))

        self.refresh()
        for choice, radio in self.radios.items():
            radio.toggled.connect(lambda on, c=choice: on and self.choose(c))
        self._loading = False

    def refresh(self) -> None:
        was, self._loading = self._loading, True
        self.radios[palette.load_appearance(self._db)].setChecked(True)
        self._loading = was

    def choice(self) -> str:
        return next(c for c, r in self.radios.items() if r.isChecked())

    def choose(self, choice: str) -> None:
        """Store *choice* and switch to it (a radio button was picked)."""
        if self._loading:
            return
        palette.save_appearance(self._db, choice)
        self._apply(choice)

    def on_show(self) -> None:
        self.refresh()


__all__ = ["AppearancePage", "SECTION_APPEARANCE"]
