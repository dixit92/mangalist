"""The owner's download switches (Settings dialog), stored in the library database's settings table.

Qt-free: the Settings dialog writes them, the adapter / jobs read them. Every getter falls back to the default when
nothing is stored (or the stored value has the wrong type), so a fresh database behaves as before the switches existed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Dict

KEY_NYAA = "downloads.nyaa"                        # dict of the NyaaOptions fields
KEY_MU_AUTOSTART = "mu_autostart"                  # the old "Auto-start MU" (config.py's key): look new series up
KEY_SCAN_AFTER_FILING = "mangapixer.scan_after_filing"   # ask MangaPixer to rescan a library after filing into it
KEY_PARTIAL_DOWNLOADS = "downloads.partial"        # a pack's release panel starts with "only the missing volumes" ticked

DEFAULTS: Dict[str, bool] = {KEY_MU_AUTOSTART: False, KEY_SCAN_AFTER_FILING: True, KEY_PARTIAL_DOWNLOADS: True}


@dataclass(frozen=True)
class NyaaOptions:
    """What the nyaa source does. ``digital_first`` and ``hide_no_seeders`` are how the search always works today
    (the ranking puts Digital first, 0-seeder results are dropped): the Settings dialog shows them as fixed."""

    enabled: bool = True
    english: bool = True                # category 3_1, English-translated literature
    raw: bool = False                   # category 3_3, Raw (Japanese)
    hide_light_novels: bool = True
    digital_first: bool = True          # fixed: the ranking
    trusted_only: bool = False
    hide_no_seeders: bool = True        # fixed: the search

    def categories(self) -> tuple:
        """The nyaa categories to ask; English when neither box is ticked (never a search of nothing)."""
        from ..services.nyaa.client import CATEGORY_ENGLISH_TRANSLATED, CATEGORY_RAW

        out = []
        if self.english or not self.raw:
            out.append(CATEGORY_ENGLISH_TRANSLATED)
        if self.raw:
            out.append(CATEGORY_RAW)
        return tuple(out)


_FIXED_ON = ("digital_first", "hide_no_seeders")


def load_nyaa_options(store: Any) -> NyaaOptions:
    stored = store.get_setting(KEY_NYAA, None)
    base = NyaaOptions()
    if not isinstance(stored, dict):
        return base
    values = {}
    for name in asdict(base):
        if name in _FIXED_ON:
            continue
        if isinstance(stored.get(name), bool):
            values[name] = stored[name]
    return replace(base, **values)


def save_nyaa_options(store: Any, options: NyaaOptions) -> None:
    data = asdict(options)
    for name in _FIXED_ON:
        data[name] = True
    store.set_setting(KEY_NYAA, data)


def get_flag(store: Any, key: str) -> bool:
    value = store.get_setting(key, None)
    return value if isinstance(value, bool) else DEFAULTS[key]


def set_flag(store: Any, key: str, on: bool) -> None:
    store.set_setting(key, bool(on))
