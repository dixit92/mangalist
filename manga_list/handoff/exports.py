"""Hand-off files for chapter downloaders (pure: text in, text out; the caller writes them).

- **gallery-dl** input file (``gallery-dl -i <file>``): per series a ``-child-filter`` line that applies to the next
  URL only, selecting the missing chapters BY NUMBER (``--chapter-range`` would select by list position) in one
  language, then the MangaDex title URL. gallery-dl's MangaDex chapter number is the whole part (``chapter``; the
  fraction is ``chapter_minor``), so a ``.5`` chapter goes with its whole number.
- **FMD2** import: the ``Config/Bookmarks`` file its *Import favorites* dialog reads in "Domdomsoft Manga
  Downloader" mode (``<MangaName>`` / ``<MangaLink>`` lines, paired in order; the site module is found by the link's
  host). FMD2 cannot take chapter ranges: the series land in its favorites and FMD2 offers their new chapters itself.
  Manga-List never writes FMD2's own database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from .._version import __version__
from .mangadex import title_url
from .wanted import CHAPTERS, Wanted

# (wanted item, MangaDex UUID or None when no MangaDex record links to the series)
Resolved = Tuple[Wanted, Optional[str]]


def gallery_dl_filter(start: int, end: int, language: str = "en") -> str:
    """The ``child-filter`` expression for chapters ``start`` .. ``end`` in ``language``."""
    return f"lang == {language!r} and {int(start)} <= chapter <= {int(end)}"


def gallery_dl_input(items: Sequence[Resolved], language: str = "en") -> str:
    """A gallery-dl input file for the missing chapters of ``items`` (series without chapters or without a MangaDex
    record are listed as comments, so the file says what it left out)."""
    lines: List[str] = [
        f"# Manga-List {__version__} - missing chapters, for gallery-dl",
        '# Run: gallery-dl -i "<this file>"   (each -child-filter line applies to the URL after it)',
        "",
    ]
    skipped: List[str] = []
    for wanted, uuid in items:
        ranges = wanted.of_kind(CHAPTERS)
        if not ranges:
            continue
        if uuid is None:
            skipped.append(f"# not found on MangaDex: {_one_line(wanted.mu_title)} (MangaUpdates {wanted.mu_id}): "
                           + ", ".join(r.describe() for r in ranges))
            continue
        for r in ranges:
            lines.append(f"# {_one_line(wanted.mu_title)} (MangaUpdates {wanted.mu_id}): {r.describe()}")
            lines.append("-child-filter = " + json.dumps(gallery_dl_filter(r.start, r.end, language)))
            lines.append(title_url(uuid))
            lines.append("")
    if skipped:
        lines += skipped + [""]
    return "\n".join(lines)


def fmd2_bookmarks(items: Iterable[Resolved]) -> str:
    """The ``Config/Bookmarks`` text for FMD2's Import favorites (Domdomsoft mode): one name / link pair per series
    with a MangaDex record."""
    lines: List[str] = []
    for wanted, uuid in items:
        if uuid is None:
            continue
        lines.append("<Manga>")
        lines.append(f"<MangaName>{_tag_text(wanted.mu_title)}</MangaName>")
        lines.append(f"<MangaLink>{title_url(uuid)}</MangaLink>")
        lines.append("</Manga>")
    return "\n".join(lines) + ("\n" if lines else "")


def write_fmd2_import(folder: Path, items: Iterable[Resolved]) -> Path:
    """Writes ``<folder>/Config/Bookmarks`` (UTF-8) and returns its path; in FMD2 choose *Import favorites*, the
    "Domdomsoft Manga Downloader" type and ``folder``."""
    path = Path(folder) / "Config" / "Bookmarks"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fmd2_bookmarks(items), encoding="utf-8")
    return path


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def _tag_text(text: str) -> str:
    """A name FMD2 reads back between its tags: one line, no angle brackets."""
    return _one_line(text).replace("<", "(").replace(">", ")")
