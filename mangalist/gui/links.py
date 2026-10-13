"""Opening a web link or a folder from the GUI - and what to do where there is no browser or file manager.

The desktop builds hand the link to the system browser. The Unraid container's desktop (the web GUI) has none: Qt
then reports failure, so the link is copied to the clipboard instead (the web GUI's clipboard panel, or the browser's
clipboard where the container syncs it) and a tooltip at the pointer says so. Every link in the GUI goes through
:func:`open_link`, so none of them silently does nothing.

Folders go through :func:`open_folder`: Explorer on Windows, the desktop's file manager elsewhere - or the program
``MANGALIST_FILE_MANAGER`` names, which is how the container (no desktop, so nothing for Qt to hand a folder to)
opens its own file manager in the web GUI.
"""

from __future__ import annotations

import logging
import os
import shlex
import sys
from pathlib import Path
from typing import Union

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import QApplication, QToolTip

log = logging.getLogger(__name__)

COPIED = "No browser here, so the link is copied - paste it into your browser:\n{url}"
FILE_MANAGER_ENV = "MANGALIST_FILE_MANAGER"
OPEN_FOLDER_LABEL = "Open folder in Explorer" if sys.platform == "win32" else "Open folder"


def open_link(url: str) -> bool:
    """Open *url* in the browser; True when it opened. Else copy it, say so at the pointer, and return False."""
    if not url:
        return False
    if QDesktopServices.openUrl(QUrl(url)):
        return True
    QApplication.clipboard().setText(url)
    QToolTip.showText(QCursor.pos(), COPIED.format(url=url))
    return False


def open_folder(folder: Union[str, Path]) -> bool:
    """Show *folder* in a file manager; True when one was started. False (logged) when none could be."""
    folder = str(folder)
    command = shlex.split(os.environ.get(FILE_MANAGER_ENV, ""))
    if command:
        started = QProcess.startDetached(command[0], [*command[1:], folder])
        if isinstance(started, tuple):                          # PySide6: (ok, pid)
            started = started[0]
        if not started:
            log.warning("Could not start %s=%r for %s", FILE_MANAGER_ENV, command[0], folder)
        return bool(started)
    if sys.platform == "win32":
        try:
            os.startfile(folder)                                # noqa: S606  (Windows-only, intentional)
        except OSError as exc:
            log.warning("Could not open %s in Explorer: %s", folder, exc)
            return False
        return True
    if QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
        return True
    log.warning("No file manager opened %s", folder)
    return False
