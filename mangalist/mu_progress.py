"""Release-progress utilities: parse English publisher notes and compute deltas.

MangaUpdates stores the English publisher's progress as a free-form ``notes``
string on each ``publishers[]`` entry (where ``type == "English"``). Conventions
observed on the site:

    "86 Chapters; Ongoing"
    "22 Volumes; Completed"
    "10 Volumes / 60 Chapters; Ongoing"
    "12 Volumes (Ongoing)"
    "Cancelled"

This module exposes a small parser that extracts (chapters, volumes, status)
from such strings, plus helpers used by ``table_model`` to render the "Behind"
column.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

_RE_CHAPTERS = re.compile(r"(\d+(?:\.\d+)?)\s*chapters?", re.IGNORECASE)
_RE_VOLUMES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:volumes?|vols?)", re.IGNORECASE)

# Words that signal the publisher has stopped releasing (case-insensitive).
_STATUS_KEYWORDS = {
    "ongoing":    "Ongoing",
    "complete":   "Completed",  # matches "completed" and "complete"
    "completed":  "Completed",
    "finished":   "Completed",
    "cancelled":  "Cancelled",
    "canceled":   "Cancelled",
    "discontinued": "Cancelled",
    "hiatus":     "Hiatus",
    "dropped":    "Dropped",
}


def parse_publisher_notes(notes: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """Return (chapters, volumes, status) parsed from a publisher ``notes`` string.

    Any of the three may be ``None`` if not present. Numbers are returned as
    ``float`` (some publishers use ``12.5``); callers may cast to int as needed.
    """
    if not notes:
        return None, None, None

    chapters: Optional[float] = None
    volumes: Optional[float] = None
    status: Optional[str] = None

    m = _RE_CHAPTERS.search(notes)
    if m:
        try:
            chapters = float(m.group(1))
        except ValueError:
            pass

    m = _RE_VOLUMES.search(notes)
    if m:
        try:
            volumes = float(m.group(1))
        except ValueError:
            pass

    lower = notes.lower()
    for kw, label in _STATUS_KEYWORDS.items():
        if kw in lower:
            status = label
            break

    return chapters, volumes, status


def english_publisher(publishers: Optional[List[Dict]]) -> Optional[Dict]:
    """Return the single best ``publishers[]`` entry whose ``type == "English"``.

    When multiple English publishers exist (e.g. a simulpub chapters publisher
    and a print volumes publisher), prefer the one whose notes contain a volume
    count over the one that only reports chapters.  Falls back to the first
    English entry if none report volumes.
    """
    if not publishers:
        return None
    english = [p for p in publishers
               if isinstance(p, dict) and (p.get("type") or "").lower() == "english"]
    if not english:
        return None
    if len(english) == 1:
        return english[0]
    # Prefer the publisher whose notes mention volumes.
    for p in english:
        _, vol, _ = parse_publisher_notes(p.get("notes") or "")
        if vol is not None:
            return p
    return english[0]


def format_behind(
    *,
    licensed: Optional[bool],
    publisher_chapters: Optional[float],
    publisher_volumes: Optional[float],
    publisher_status: Optional[str],
    scan_latest_chapter: Optional[float],
    scan_latest_volume: Optional[float] = None,
    disk_max_chapter: Optional[float],
    disk_max_volume: Optional[float],
    anilist_chapters: Optional[float] = None,
    anilist_volumes: Optional[float] = None,
    prefer_volumes: bool = False,
) -> str:
    """Build the short text shown in the 'Behind' column.

    Produces up to two delta parts joined by " · ":
      - Volume delta (official publisher or scanlation volumes)
      - Chapter delta (official publisher or scanlation chapters)

    Cross-unit estimation via AniList is used when the disk has one unit
    but the only available progress data is in the other unit.
    """
    parts: List[str] = []

    if licensed is True:
        # --- Volume part ---
        vol_part = _delta_str(
            official=publisher_volumes, disk=disk_max_volume,
            unit="vol", label="official",
        )
        if vol_part is None:
            vol_part = _anilist_cross_unit(
                have_chapters=publisher_chapters, disk_vol=disk_max_volume,
                al_chapters=anilist_chapters, al_volumes=anilist_volumes,
                unit="vol", label="official (~)",
            )
        if vol_part:
            parts.append(vol_part)

        # --- Chapter part ---
        ch_part = _delta_str(
            official=publisher_chapters, disk=disk_max_chapter,
            unit="ch", label="official",
        )
        if ch_part is None:
            ch_part = _anilist_cross_unit(
                have_chapters=publisher_volumes, disk_vol=disk_max_chapter,
                al_chapters=anilist_volumes, al_volumes=anilist_chapters,
                unit="ch", label="official (~)",
            )
        if ch_part:
            # Scanlation divergence suffix.
            if scan_latest_chapter is not None and publisher_chapters is not None:
                scan_delta = scan_latest_chapter - publisher_chapters
                if scan_delta >= 1:
                    ch_part += f" · scan +{_fmt_n(scan_delta)}"
                elif scan_delta <= -1 and (publisher_status or "").lower() != "completed":
                    ch_part += " · scan stopped"
            parts.append(ch_part)

    else:
        # --- Unlicensed: scanlation sources ---
        # Chapter part
        ch_part = _delta_str(
            official=scan_latest_chapter, disk=disk_max_chapter,
            unit="ch", label="(scan)",
        )
        if ch_part is None:
            ch_part = _anilist_cross_unit(
                have_chapters=scan_latest_volume, disk_vol=disk_max_chapter,
                al_chapters=anilist_volumes, al_volumes=anilist_chapters,
                unit="ch", label="(scan ~)",
            )
        if ch_part:
            parts.append(ch_part)

        # Volume part
        vol_part = _delta_str(
            official=scan_latest_volume, disk=disk_max_volume,
            unit="vol", label="(scan)",
        )
        if vol_part is None:
            vol_part = _anilist_cross_unit(
                have_chapters=scan_latest_chapter, disk_vol=disk_max_volume,
                al_chapters=anilist_chapters, al_volumes=anilist_volumes,
                unit="vol", label="(scan ~)",
            )
        if vol_part:
            parts.append(vol_part)

    if not parts:
        return ""
    # Deduplicate: if both say "Up to date", show once.
    unique = list(dict.fromkeys(parts))
    if unique == ["Up to date"]:
        return "Up to date"
    return "  ·  ".join(unique)


def format_behind_tooltip(
    *,
    licensed: Optional[bool],
    publisher_name: Optional[str],
    publisher_chapters: Optional[float],
    publisher_volumes: Optional[float],
    publisher_status: Optional[str],
    scan_latest_chapter: Optional[float],
    scan_latest_volume: Optional[float] = None,
    disk_max_chapter: Optional[float],
    disk_max_volume: Optional[float],
    anilist_chapters: Optional[float] = None,
    anilist_volumes: Optional[float] = None,
    prefer_volumes: bool = False,
) -> str:
    parts: List[str] = []
    if licensed is True and publisher_name:
        bits = [f"English publisher: {publisher_name}"]
        nums = []
        if publisher_volumes is not None:
            nums.append(f"{_fmt_n(publisher_volumes)} vol")
        if publisher_chapters is not None:
            nums.append(f"{_fmt_n(publisher_chapters)} ch")
        if nums:
            bits.append("(" + ", ".join(nums) + ")")
        if publisher_status:
            bits.append(f"[{publisher_status}]")
        parts.append(" ".join(bits))
    if scan_latest_volume is not None:
        parts.append(f"Scanlation latest: vol.{_fmt_n(scan_latest_volume)}")
    if scan_latest_chapter is not None:
        parts.append(f"Scanlation latest: ch.{_fmt_n(scan_latest_chapter)}")
    if anilist_volumes is not None or anilist_chapters is not None:
        al_bits = []
        if anilist_volumes is not None:
            al_bits.append(f"{_fmt_n(anilist_volumes)} vol")
        if anilist_chapters is not None:
            al_bits.append(f"{_fmt_n(anilist_chapters)} ch")
        parts.append("AniList: " + ", ".join(al_bits))
    if disk_max_volume is not None:
        parts.append(f"On disk: vol.{_fmt_n(disk_max_volume)}")
    if disk_max_chapter is not None:
        parts.append(f"On disk: ch.{_fmt_n(disk_max_chapter)}")
    return "\n".join(parts)


def behind_sort_key(
    *,
    licensed: Optional[bool],
    publisher_chapters: Optional[float],
    publisher_volumes: Optional[float],
    scan_latest_chapter: Optional[float],
    scan_latest_volume: Optional[float] = None,
    disk_max_chapter: Optional[float],
    disk_max_volume: Optional[float],
    anilist_chapters: Optional[float] = None,
    anilist_volumes: Optional[float] = None,
    prefer_volumes: bool = False,
) -> float:
    """Sort: largest positive delta first; 0 = up to date; -inf = no data.

    Uses the maximum delta across all available unit comparisons.
    """
    deltas: List[float] = []
    if licensed is True:
        if publisher_volumes is not None and disk_max_volume is not None:
            deltas.append(publisher_volumes - disk_max_volume)
        if publisher_chapters is not None and disk_max_chapter is not None:
            deltas.append(publisher_chapters - disk_max_chapter)
    else:
        if scan_latest_chapter is not None and disk_max_chapter is not None:
            deltas.append(scan_latest_chapter - disk_max_chapter)
        if scan_latest_volume is not None and disk_max_volume is not None:
            deltas.append(scan_latest_volume - disk_max_volume)
    return max(deltas) if deltas else float("-inf")


# --- internals -----------------------------------------------------------

def _delta_str(
    official: Optional[float],
    disk: Optional[float],
    unit: str,
    label: str,
) -> Optional[str]:
    """Return a formatted delta string, or None if either value is missing."""
    if official is None or disk is None:
        return None
    delta = official - disk
    if delta > 0:
        return f"+{_fmt_n(delta)} {unit} {label}"
    if delta == 0:
        return "Up to date"
    return "Disk ahead"  # disk has more than official/scan reports


def _anilist_cross_unit(
    have_chapters: Optional[float],
    disk_vol: Optional[float],
    al_chapters: Optional[float],
    al_volumes: Optional[float],
    unit: str,
    label: str,
) -> Optional[str]:
    """Estimate a delta in *unit* from the opposite unit via AniList ratio.

    *have_chapters* is the known chapter count (from publisher or scan).
    *disk_vol* is the disk value in the target *unit*.
    *al_chapters* / *al_volumes* are the AniList totals used to derive the
    chapters-per-volume ratio.

    Returns a formatted string (marked with ~) or None.
    """
    if have_chapters is None or disk_vol is None:
        return None
    if al_chapters is None or al_volumes is None or al_volumes == 0:
        return None
    ch_per_vol = al_chapters / al_volumes
    if ch_per_vol <= 0:
        return None
    # Convert have_chapters → equivalent volumes (or vice-versa)
    estimated_official = have_chapters / ch_per_vol if unit == "vol" else have_chapters * ch_per_vol
    delta = estimated_official - disk_vol
    if abs(delta) < 0.5:
        return "Up to date"
    if delta > 0:
        return f"~+{_fmt_n(round(delta, 1))} {unit} {label}"
    return "~Disk ahead"


def _fmt_n(n: float) -> str:
    """Format a release-count number without trailing ``.0``."""
    if n == int(n):
        return str(int(n))
    return f"{n:g}"
