"""Which program downloads chapters, as the GUI words it (transition fixes A1, A2, A4; 2026-10-24).

The chapter backend is a Settings choice (Suwayomi, or the Built-in extensions - MangaList's own extension host) and the
owner can switch it while screens are open. Every screen that names the program asks here, at the moment it shows the
text: the GUI backend's ``chapter_backend()`` says which one is in force (a backend without it - older fakes, downloads
off - is Suwayomi). Texts are written for Suwayomi and :func:`reword` turns them into the host's wording through
:func:`mangalist.downloads.chapter_backend.wording`, the one place that knows the replacements.

No Qt.
"""

from __future__ import annotations

from typing import Optional

from ..downloads import chapter_backend
from ..downloads.contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKEND_SUWAYOMI

#: The chooser's two entries (key, text): Settings > Connected services and Settings > Download sources.
CHOICES = ((CHAPTER_BACKEND_SUWAYOMI, chapter_backend.LABELS[CHAPTER_BACKEND_SUWAYOMI]),
           (CHAPTER_BACKEND_EXTENSIONS, chapter_backend.LABELS[CHAPTER_BACKEND_EXTENSIONS]))


def backend_key(backend) -> str:
    """``'suwayomi'`` or ``'extensions'``: the chapter backend in force (Suwayomi when the backend cannot say)."""
    chosen = getattr(backend, "chapter_backend", None) if backend is not None else None
    try:
        return CHAPTER_BACKEND_EXTENSIONS if callable(chosen) and chosen() == CHAPTER_BACKEND_EXTENSIONS \
            else CHAPTER_BACKEND_SUWAYOMI
    except Exception:  # noqa: BLE001 - wording must never break a screen
        return CHAPTER_BACKEND_SUWAYOMI


def is_builtin(backend) -> bool:
    return backend_key(backend) == CHAPTER_BACKEND_EXTENSIONS


def label_of(backend) -> str:
    """The choice as the chooser names it: "Suwayomi" / "Built-in extensions"."""
    return chapter_backend.LABELS[backend_key(backend)]


def program(backend) -> str:
    """What a sentence calls the program: "Suwayomi" / "the extension host"."""
    return chapter_backend.NAMES[backend_key(backend)]


#: The Download sources card is "Chapter sources" under either backend (it lists the sources of the one in force).
CARD_TITLE = "Chapter sources"


def reword_for(builtin: bool, text: Optional[str]) -> Optional[str]:
    """*text*, written for Suwayomi, worded for the program in force. "Suwayomi sources" (the card's old name) is "chapter
    sources" under either backend; a source line "Suwayomi · MangaDex (EN)" (a finding's) names the choice, "Built-in
    extensions · MangaDex (EN)"; otherwise Suwayomi's own wording stays as it is."""
    if text and "Suwayomi sources" in text:
        text = text.replace("Suwayomi sources", CARD_TITLE.lower())
    if text and builtin and "Suwayomi source" in text:
        text = text.replace("Suwayomi source", "chapter source")
    if text and builtin and "Suwayomi · " in text:
        text = text.replace("Suwayomi · ", f"{chapter_backend.LABELS[CHAPTER_BACKEND_EXTENSIONS]} · ")
    return chapter_backend.wording(text, builtin)


def reword(backend, text: Optional[str]) -> Optional[str]:
    """:func:`reword_for` the backend in force."""
    return reword_for(is_builtin(backend), text)
