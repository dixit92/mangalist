"""Where the shell gets the other lanes' parts (UI cycle, ``gui/shell.py``): lane B's Download tab and Settings dialog,
lane C's duplicates view and finder. One place, so the shell's tests can stand fakes in for them."""

from __future__ import annotations

from typing import Callable


def download_tab_class():
    """Lane B's ``DownloadTab`` class."""
    from .download_tab import DownloadTab

    return DownloadTab


def open_settings_function() -> Callable:
    """Lane B's ``open_settings(parent, db, backend, section=None) -> SettingsResult``."""
    from .settings_dialog import open_settings

    return open_settings


def duplicates_view_class():
    """Lane C's ``DuplicatesView`` class."""
    from .duplicates_view import DuplicatesView

    return DuplicatesView


def find_duplicate_files_function() -> Callable:
    """Lane C's ``find_duplicate_files(db, root_ids=None)``."""
    from ..duplicates import find_duplicate_files

    return find_duplicate_files
