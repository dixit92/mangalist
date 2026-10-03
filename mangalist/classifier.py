"""Filename heuristics + per-folder scoring."""

from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

from .models import FileHit, MangaEntry, Verdict

# --- Tunable constants -------------------------------------------------------

# Size thresholds in bytes
LARGE_FILE_BYTES = 50 * 1024 * 1024   # >= 50 MB nudges toward "volume"
SMALL_FILE_BYTES = 30 * 1024 * 1024   # <  30 MB nudges toward "chapter"
TINY_FILE_BYTES  =  5 * 1024 * 1024   # <  5 MB: "volume" token likely an announcement, not a real volume archive

MANY_FILES_THRESHOLD = 10             # >=10 files + small median => chapters

# Score weights
W_VOL_FILE = 1.0
W_CH_FILE = 1.0
W_LARGE_MEDIAN_BONUS = 3.0
W_SMALL_MANY_BONUS = 4.0
W_BOTH_PATTERN = 6.0                  # parent volumes + subfolder chapters
W_BOTH_COEXIST = 2.0                  # both kinds at depth 0 in non-trivial counts

# --- Regexes -----------------------------------------------------------------

# Match Vol / Vol. / Volume followed by a number (with optional whitespace/punct).
# Also bare `v01` / `v1` style tokens.
_RE_VOLUME = re.compile(
    r"(?<![A-Za-z])(?:vol(?:ume)?\.?|v)\s*[_\-.]?\s*\d+",
    re.IGNORECASE,
)

# Match Ch / Ch. / Chp / Chap / Chapter followed by a number, or bare `c003` (>=2 digits).
_RE_CHAPTER = re.compile(
    r"(?<![A-Za-z])(?:ch(?:apter|ap|p|\.)?|c)\s*[_\-.]?\s*\d{1,4}(?:\.\d+)?",
    re.IGNORECASE,
)

# Matches announcement/promotional phrases that appear after a volume token in a filename,
# indicating the "Volume N" text is not describing the file's content.
_RE_VOL_ANNOUNCEMENT = re.compile(
    r"(?:notice|announcement|on\s+sale|now\s+on\s+sale|preview|promo(?:tion)?"
    r"|release|coming\s+soon)",
    re.IGNORECASE,
)

# Folder name parser:
#   "<title>"
#   "<title> [<english>]"
_RE_FOLDER = re.compile(
    r"^\s*(?P<title>.+?)(?:\s*\[(?P<eng>[^\]]+)\])?\s*$",
)


# --- Public API --------------------------------------------------------------

def parse_folder_name(name: str) -> Tuple[str, Optional[str]]:
    """Return (title, english_title_or_None) from a manga folder name.

    The folder name may be:
      * romanized only          -> ("7-Nin no Nemuri Hime", None)
      * english only            -> ("A Dating Sim of Life or Death", None)
      * romanized [english]     -> ("Jitsu wa Ore...", "Am I Actually...")
    """
    m = _RE_FOLDER.match(name)
    if not m:
        return name.strip(), None
    title = m.group("title").strip()
    eng = m.group("eng")
    return title, eng.strip() if eng else None


def detect_tokens(filename: str, file_size: int = 0) -> Tuple[bool, bool]:
    """Return (has_volume, has_chapter) for a single filename.

    ``file_size`` (bytes) is used to demote suspicious volume tokens:
    if the file is tiny (< TINY_FILE_BYTES) and the filename contains an
    announcement phrase (e.g. "Volume 2 Notice"), the volume token is ignored.
    """
    # Strip extension so things like ".cbz" don't interact with the regex.
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    has_vol = bool(_RE_VOLUME.search(stem))
    has_ch = bool(_RE_CHAPTER.search(stem))

    # Demote volume token when it looks like a promotional announcement rather
    # than a real volume archive.  Two independent signals must align:
    #   1. filename contains an announcement keyword, AND
    #   2. file is suspiciously small for a volume archive (< 5 MB)
    if has_vol and not has_ch:
        if file_size > 0 and file_size < TINY_FILE_BYTES:
            if _RE_VOL_ANNOUNCEMENT.search(stem):
                has_vol = False

    return has_vol, has_ch


def annotate_file(hit: FileHit) -> None:
    """Populate has_volume / has_chapter on a FileHit in place."""
    has_vol, has_ch = detect_tokens(hit.path.name, file_size=hit.size)
    hit.has_volume = has_vol
    hit.has_chapter = has_ch


def classify(entry: MangaEntry) -> MangaEntry:
    """Score the entry, populate vol_pct / ch_pct / both_pct / verdict / reasons."""
    reasons: List[str] = []

    n_vol = entry.n_volume_files
    n_ch = entry.n_chapter_files
    median = entry.median_size
    n_files = entry.n_files

    vol_score = W_VOL_FILE * n_vol
    ch_score = W_CH_FILE * n_ch
    both_score = 0.0

    if n_vol:
        reasons.append(f"{n_vol} volume-tagged file(s)")
    if n_ch:
        reasons.append(f"{n_ch} chapter-tagged file(s)")
    if entry.n_ambiguous:
        reasons.append(f"{entry.n_ambiguous} file(s) without vol/chapter tokens")

    # Size-based nudges
    if n_files and median >= LARGE_FILE_BYTES:
        vol_score += W_LARGE_MEDIAN_BONUS
        reasons.append(f"median size {_human(median)} >= 50 MB (volume-ish)")
    if n_files >= MANY_FILES_THRESHOLD and 0 < median < SMALL_FILE_BYTES:
        ch_score += W_SMALL_MANY_BONUS
        reasons.append(
            f"{n_files} files with median {_human(median)} < 30 MB (chapter-ish)"
        )

    # "Both" patterns
    if entry.parent_volume_files >= 1 and entry.subfolder_chapter_files >= 1:
        both_score += W_BOTH_PATTERN
        reasons.append(
            f"{entry.parent_volume_files} volume(s) at root + "
            f"{entry.subfolder_chapter_files} chapter(s) in subfolder(s)"
        )

    depth0_vol = sum(1 for f in entry.files if f.kind == "volume" and f.depth == 0)
    depth0_ch = sum(1 for f in entry.files if f.kind == "chapter" and f.depth == 0)
    if depth0_vol >= 1 and depth0_ch >= 1:
        both_score += W_BOTH_COEXIST
        reasons.append(
            f"volumes ({depth0_vol}) and chapters ({depth0_ch}) coexist at folder root"
        )

    total = vol_score + ch_score + both_score
    if total <= 0:
        entry.vol_pct = entry.ch_pct = entry.both_pct = 0.0
        entry.verdict = Verdict.UNKNOWN
        if not reasons:
            reasons.append("No recognizable volume/chapter tokens found")
        entry.reasons = reasons
        return entry

    entry.vol_pct = round(100.0 * vol_score / total, 1)
    entry.ch_pct = round(100.0 * ch_score / total, 1)
    entry.both_pct = round(100.0 * both_score / total, 1)

    # Structural override: if there is at least one volume at the manga-folder
    # root AND at least one chapter inside any subfolder, the entry has BOTH
    # forms regardless of which raw count is larger.
    structural_both = (
        entry.parent_volume_files >= 1 and entry.subfolder_chapter_files >= 1
    )

    # Co-existence at depth 0 is also "Both" but only when both kinds are
    # non-trivial (>= 2 each) to avoid flipping a single stray-named file.
    coexist_both = depth0_vol >= 2 and depth0_ch >= 2

    if structural_both or coexist_both:
        entry.verdict = Verdict.BOTH
    else:
        # Verdict via arg-max with tie-break Both > Chapters > Volumes
        scores = [
            (entry.both_pct, Verdict.BOTH),
            (entry.ch_pct, Verdict.CHAPTERS),
            (entry.vol_pct, Verdict.VOLUMES),
        ]
        scores.sort(key=lambda x: x[0], reverse=True)
        entry.verdict = scores[0][1] if scores[0][0] > 0 else Verdict.UNKNOWN

    entry.reasons = reasons
    return entry


def classify_all(entries: Iterable[MangaEntry]) -> List[MangaEntry]:
    return [classify(e) for e in entries]


# --- helpers -----------------------------------------------------------------

def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"
