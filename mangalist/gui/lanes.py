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


def find_same_series_function() -> Callable:
    """``find_same_series(db) -> SameSeriesReport`` (:mod:`mangalist.same_series`)."""
    from ..same_series import find_same_series

    return find_same_series


def creator_name_clients():
    """``(get_series, get_author, not_found)`` of MangaUpdates for the creator-name lookup (:mod:`mangalist.creator_names`),
    or None when none is to be asked (the tests: no test asks MangaUpdates)."""
    from .. import mu_client

    return mu_client.get_series, mu_client.get_author, mu_client.is_not_found
