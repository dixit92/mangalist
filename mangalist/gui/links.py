"""Opening a web link from the GUI - and what to do where there is no browser.

The desktop builds hand the link to the system browser. The Unraid container's desktop (the web GUI) has none: Qt
then reports failure, so the link is copied to the clipboard instead (the web GUI's clipboard panel, or the browser's
clipboard where the container syncs it) and a tooltip at the pointer says so. Every link in the GUI goes through
:func:`open_link`, so none of them silently does nothing.
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QCursor, QDesktopServices
from PySide6.QtWidgets import QApplication, QToolTip

COPIED = "No browser here, so the link is copied - paste it into your browser:\n{url}"


def open_link(url: str) -> bool:
    """Open *url* in the browser; True when it opened. Else copy it, say so at the pointer, and return False."""
    if not url:
        return False
    if QDesktopServices.openUrl(QUrl(url)):
        return True
    QApplication.clipboard().setText(url)
    QToolTip.showText(QCursor.pos(), COPIED.format(url=url))
    return False
