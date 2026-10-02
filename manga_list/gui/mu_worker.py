"""Background worker that enriches MangaEntry objects with MangaUpdates data.

Pipeline per entry (runs in a worker thread):
  1. If a confirmed match is cached and licensed==True, apply cache and skip API call.
  2. If a confirmed match is cached but licensed is unknown/False, re-fetch licensed flag.
  3. Otherwise: the stage-2 matcher (``mu_match.match_entry``) classifies the
     folder, searches MU with its query variants (automatic type filter; page 2
     on a tie), scores the candidates and bands the result: auto / review are stored as an
     unconfirmed match (review gets the amber highlight), unmatched and
     not-one-work folders clear an unconfirmed older match.

Rate limiting: REQUEST_DELAY seconds between every outbound HTTP call.
"""

from __future__ import annotations

import logging
import time
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from ..models import MangaEntry
from .. import anilist_client, mu_cache, mu_client
from ..matcher.mangaupdates import AUTO_SEARCH_FILTER, SEARCH_PAGE_SIZE, map_search_page, map_series
from ..mu_match import BAND_NOT_A_WORK, BAND_UNMATCHED, candidate_titles, match_entry
from ..mu_progress import english_publisher, parse_publisher_notes

_log = logging.getLogger(__name__)


def _clear_examined_if_newly_licensed(entry: MangaEntry, new_licensed: bool | None) -> None:
    """Clear the examined flag if entry is becoming newly licensed.

    This alerts the user to re-review entries that were marked as examined
    but are now found to be licensed.
    """
    # Only clear if transitioning to licensed=True from not licensed
    if new_licensed is True and entry.licensed is not True:
        entry.examined = False


class MuWorker(QObject):
    """Process a list of entries, emitting ``entry_updated`` after each one.

    Signals
    -------
    entry_updated(entry, row)
        Emitted after an entry has been enriched. *row* is the source-model row.
    finished()
        Emitted when all entries have been processed.
    """

    entry_started = Signal(int)            # source_row — about to fetch
    entry_updated = Signal(object, int)   # (MangaEntry, source_row)
    finished = Signal()

    def __init__(self, entries: List[tuple]):
        """*entries* is a list of (source_row, MangaEntry) pairs to process."""
        super().__init__()
        self._entries = entries
        self._abort = False

    def abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        last_started: Optional[tuple] = None
        total = len(self._entries)
        _log.info("MU scan started — %d entries", total)
        for row, entry in self._entries:
            if self._abort:
                _log.info("MU scan aborted at row %d", row)
                break
            self.entry_started.emit(row)
            last_started = (row, entry)
            try:
                self._process(row, entry)
                last_started = None  # _process emits entry_updated itself
            except Exception:  # noqa: BLE001
                _log.exception("Unhandled error processing row %d (%s)", row, entry.title)
                self.entry_updated.emit(entry, row)  # clear the spinner even on error
                last_started = None
        # If aborted mid-item, clear the spinner for the last highlighted row.
        if last_started is not None:
            self.entry_updated.emit(last_started[1], last_started[0])
        _log.info("MU scan finished")
        self.finished.emit()

    def _process(self, row: int, entry: MangaEntry) -> None:
        folder = entry.folder
        cached = mu_cache.load_entry(folder)

        if cached:
            # Apply cached data to the in-memory entry regardless of what
            # we do next (avoids a blank cell until the worker gets there).
            _apply_cache(entry, cached)

            if cached.get("mu_confirmed"):
                # Confirmed match — always refresh progress so Behind stays current.
                _log.debug("Refreshing progress for confirmed match: %s", entry.title)
                mu_id_cached = cached.get("mu_id")
                if not mu_id_cached:
                    _log.warning("Skipping refresh for %s — mu_id is None in cache", entry.title)
                    self.entry_updated.emit(entry, row)
                    return
                time.sleep(mu_client.REQUEST_DELAY)
                progress = _detail_progress(None)  # defaults
                licensed = cached.get("licensed")
                try:
                    detail = mu_client.get_series(mu_id_cached)
                    licensed = detail.get("licensed")
                    progress = _detail_progress(detail)
                except Exception:  # noqa: BLE001
                    _log.warning("get_series failed for %s (id=%s)", entry.title, mu_id_cached, exc_info=True)
                # Clear examined if becoming licensed
                _clear_examined_if_newly_licensed(entry, licensed)
                entry.licensed = licensed

                # Fetch latest scanlated volume for unlicensed series.
                extra = _fetch_extra(
                    mu_id=mu_id_cached,
                    mu_title=cached.get("mu_title") or entry.title,
                    licensed=licensed,
                    progress=progress,
                    cached_anilist_id=cached.get("anilist_id"),
                )
                progress.update(extra)
                _apply_progress(entry, progress)
                mu_cache.save_entry(
                    folder, cached["mu_id"], cached.get("mu_title") or "",
                    cached.get("mu_url") or "", licensed, mu_confirmed=True,
                    mu_associated=cached.get("mu_associated") or [],
                    mu_score=float(cached.get("mu_score") or 0.0),
                    # Not re-scored: keep what the stored score means.
                    mu_score_version=cached.get("mu_score_version") or 1,
                    mu_band=cached.get("mu_band"),
                    mu_reasons=cached.get("mu_reasons") or [],
                    **progress,
                )
                self.entry_updated.emit(entry, row)
                return

        # --- Fresh lookup: the stage-2 matcher decides auto / review / unmatched ---
        # Full records fetched by the retrieval loop are kept, so the chosen one is not fetched twice.
        details: Dict[str, dict] = {}

        def search(text: str, page: int):
            time.sleep(mu_client.REQUEST_DELAY)
            response = mu_client.search_series_page(text, page=page, page_size=SEARCH_PAGE_SIZE,
                                                    filter_types=list(AUTO_SEARCH_FILTER))
            return map_search_page(response)

        def get(external_id: str):
            # A record MangaUpdates no longer has (404) is dropped from the candidates; any other failure
            # ends this lookup (the entry keeps what it had and the next Check MU tries again).
            time.sleep(mu_client.REQUEST_DELAY)
            try:
                detail = mu_client.get_series(int(external_id))
            except Exception as exc:  # noqa: BLE001
                if mu_client.is_not_found(exc):
                    return None
                raise
            details[external_id] = detail
            return map_series(detail, external_id)

        try:
            match = match_entry(entry, search, get)
        except Exception:  # noqa: BLE001
            _log.warning("MU lookup failed for '%s'", entry.title, exc_info=True)
            self.entry_updated.emit(entry, row)
            return

        entry.mu_work_class = match.classification.cls.name
        if match.band in (BAND_UNMATCHED, BAND_NOT_A_WORK):
            _log.info("No MU match for '%s': %s (%s)", entry.title, match.band,
                      "; ".join(match.classification.reasons))
            if cached:
                # An unconfirmed match from an earlier lookup: the matcher no longer supports it.
                mu_cache.delete_entry(folder)
                _clear_match(entry)
            entry.mu_band = match.band
            self.entry_updated.emit(entry, row)
            return

        top = match.top.candidate
        mu_id = int(top.external_id)
        mu_title = top.title or entry.title
        assoc = candidate_titles(top)

        # Licensed flag + publisher/scan progress from the full record.
        detail = details.get(top.external_id)
        if detail is None:
            try:
                time.sleep(mu_client.REQUEST_DELAY)
                detail = mu_client.get_series(mu_id)
            except Exception:  # noqa: BLE001
                _log.warning("get_series failed for '%s' (id=%s)", entry.title, mu_id, exc_info=True)
        licensed: Optional[bool] = detail.get("licensed") if detail else None
        mu_url = (detail or {}).get("url") or ""
        progress = _detail_progress(detail)

        extra = _fetch_extra(
            mu_id=mu_id,
            mu_title=mu_title,
            licensed=licensed,
            progress=progress,
            cached_anilist_id=None,
        )
        progress.update(extra)

        _log.info("Matched '%s' -> '%s' (%s, title score=%.3f, reasons=%s, licensed=%s)",
                  entry.title, mu_title, match.band, match.title_score, ",".join(match.reasons) or "-", licensed)
        # Clear examined if becoming licensed
        _clear_examined_if_newly_licensed(entry, licensed)
        entry.mu_id = mu_id
        entry.mu_title = mu_title
        entry.mu_url = mu_url
        entry.licensed = licensed
        entry.mu_confirmed = False
        entry.mu_associated = assoc
        entry.mu_score = match.title_score
        entry.mu_score_version = mu_cache.MU_SCORE_VERSION
        entry.mu_band = match.band
        entry.mu_reasons = list(match.reasons)
        _apply_progress(entry, progress)

        mu_cache.save_entry(folder, mu_id, mu_title, mu_url, licensed,
                            mu_confirmed=False, mu_associated=assoc,
                            mu_score=match.title_score, mu_band=match.band,
                            mu_reasons=list(match.reasons), **progress)
        self.entry_updated.emit(entry, row)


def _clear_match(entry: MangaEntry) -> None:
    """Forget an unconfirmed match on the in-memory entry (the cache row is deleted separately),
    including the progress read from its record, so Behind / Completed do not show stale data."""
    entry.mu_id = None
    entry.mu_title = None
    entry.mu_url = None
    entry.licensed = None
    entry.mu_confirmed = False
    entry.mu_associated = []
    entry.mu_score = 0.0
    entry.mu_score_version = mu_cache.MU_SCORE_VERSION
    entry.mu_band = None
    entry.mu_reasons = []
    entry.scan_latest_chapter = None
    entry.scan_latest_volume = None
    entry.publisher_name = None
    entry.publisher_chapters = None
    entry.publisher_volumes = None
    entry.publisher_status = None
    entry.anilist_id = None
    entry.anilist_chapters = None
    entry.anilist_volumes = None
    entry.completed_in_origin = None
    entry.behind_override = None


def _apply_cache(entry: MangaEntry, cached: dict) -> None:
    entry.mu_id = cached.get("mu_id")
    entry.mu_title = cached.get("mu_title")
    entry.mu_url = cached.get("mu_url")
    entry.licensed = cached.get("licensed")
    entry.mu_confirmed = cached.get("mu_confirmed", False)
    entry.mu_associated = cached.get("mu_associated") or []
    entry.mu_score = float(cached.get("mu_score") or 0.0)
    entry.mu_score_version = int(cached.get("mu_score_version") or 1)
    entry.mu_band = cached.get("mu_band")
    entry.mu_reasons = list(cached.get("mu_reasons") or [])
    # Tolerate older caches missing the new keys.
    entry.scan_latest_chapter = _opt_float(cached.get("scan_latest_chapter"))
    entry.publisher_name = cached.get("publisher_name")
    entry.publisher_chapters = _opt_float(cached.get("publisher_chapters"))
    entry.publisher_volumes = _opt_float(cached.get("publisher_volumes"))
    entry.publisher_status = cached.get("publisher_status")
    entry.scan_latest_volume = _opt_float(cached.get("scan_latest_volume"))
    entry.anilist_id = cached.get("anilist_id")
    entry.anilist_chapters = _opt_float(cached.get("anilist_chapters"))
    entry.anilist_volumes = _opt_float(cached.get("anilist_volumes"))
    # Tolerate older caches missing completed_in_origin.
    co = cached.get("completed_in_origin")
    entry.completed_in_origin = None if co is None else bool(co)
    entry.behind_override = cached.get("behind_override") or None


def _opt_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _detail_progress(detail: Optional[dict]) -> dict:
    """Extract progress fields from a SeriesModelV1 detail dict.

    Returns a dict with the exact kwargs expected by ``mu_cache.save_entry``
    (excluding scan_latest_volume / anilist_* which are fetched separately).
    """
    if not detail:
        return {
            "scan_latest_chapter": None,
            "publisher_name": None,
            "publisher_chapters": None,
            "publisher_volumes": None,
            "publisher_status": None,
            "completed_in_origin": None,
        }

    scan_latest = detail.get("latest_chapter")
    try:
        scan_latest = float(scan_latest) if scan_latest is not None else None
    except (TypeError, ValueError):
        scan_latest = None

    pub = english_publisher(detail.get("publishers"))
    pub_name = pub.get("publisher_name") if pub else None
    pub_notes = pub.get("notes") if pub else ""
    pub_ch, pub_vol, pub_status = parse_publisher_notes(pub_notes or "")

    # Fallback: if publisher notes didn't yield a volume count, try the
    # top-level "Status in Country of Origin" string (e.g. "23 Volumes (Complete)").
    if pub_vol is None:
        status_str = detail.get("status") or ""
        _, status_vol, status_status = parse_publisher_notes(status_str)
        if status_vol is not None:
            pub_vol = status_vol
        if pub_status is None and status_status is not None:
            pub_status = status_status

    # Final fallback: use the boolean `completed` field for status.
    if pub_status is None and detail.get("completed") is True:
        pub_status = "Completed"

    completed_val = detail.get("completed")
    _log.debug("completed field raw value: %r (type=%s)", completed_val, type(completed_val).__name__)
    return {
        "scan_latest_chapter": scan_latest,
        "publisher_name": pub_name,
        "publisher_chapters": pub_ch,
        "publisher_volumes": pub_vol,
        "publisher_status": pub_status,
        "completed_in_origin": bool(completed_val) if completed_val is not None else None,
    }


def _fetch_extra(
    *,
    mu_id: int,
    mu_title: str,
    licensed: Optional[bool],
    progress: dict,
    cached_anilist_id: Optional[int],
) -> dict:
    """Fetch scan_latest_volume and AniList data where needed.

    Returns a dict of extra keys to merge into *progress*.

    Logic:
    - Unlicensed: always fetch latest scanlated volume from MU releases.
    - AniList: fetch when one side (publisher/scan) has chapters and the
      other side (disk) has volumes, or vice versa — needed for cross-unit
      estimation in format_behind.  ``cached_anilist_id`` is used for direct
      lookup to avoid repeated searches.
    """
    extra: dict = {}

    # Scanlated volume: only meaningful for unlicensed series.
    if licensed is not True:
        scan_vol = _fetch_scan_volume(mu_id)
        extra["scan_latest_volume"] = scan_vol
        _log.debug("scan_latest_volume for %r: %s", mu_title, scan_vol)
    else:
        extra["scan_latest_volume"] = None

    # AniList: fetch when we have chapter data but no volume data, or
    # volume data but no chapter data, so the UI can do cross-unit math.
    pub_ch = progress.get("publisher_chapters")
    pub_vol = progress.get("publisher_volumes")
    scan_ch = progress.get("scan_latest_chapter")
    scan_vol_new = extra.get("scan_latest_volume")
    has_ch_data = pub_ch is not None or scan_ch is not None
    has_vol_data = pub_vol is not None or scan_vol_new is not None
    need_anilist = has_ch_data != has_vol_data  # XOR: one side missing

    if need_anilist or cached_anilist_id:
        al = _fetch_anilist(mu_title, cached_anilist_id)
        if al:
            extra["anilist_id"] = al.get("id")
            extra["anilist_chapters"] = al.get("chapters")
            extra["anilist_volumes"] = al.get("volumes")
            _log.debug("AniList data for %r: %s", mu_title, al)

    return extra


def _fetch_scan_volume(mu_id: int) -> Optional[float]:
    """Return the highest scanlated volume number seen in recent MU releases.

    Queries POST /releases/search ordered by date desc; takes the max volume
    value across the returned records.  Returns None if no volume info found.
    """
    try:
        time.sleep(mu_client.REQUEST_DELAY)
        releases = mu_client.get_latest_releases(mu_id, page_size=10)
    except Exception:  # noqa: BLE001
        _log.warning("get_latest_releases failed for mu_id=%s", mu_id, exc_info=True)
        return None
    best: Optional[float] = None
    for rel in releases:
        vol_str = rel.get("volume")
        if not vol_str:
            continue
        try:
            # Volume strings from MU are usually plain integers ("5") or
            # ranges ("5-6"); take the upper bound.
            parts = str(vol_str).replace("-", " ").split()
            v = max(float(p) for p in parts if p.replace(".", "", 1).isdigit())
            if best is None or v > best:
                best = v
        except (ValueError, TypeError):
            continue
    return best


def _fetch_anilist(title: str, cached_id: Optional[int]) -> Optional[dict]:
    """Return AniList data dict (id, chapters, volumes) or None.

    Uses *cached_id* for a direct lookup when available; otherwise searches
    by *title*.  Returns None on any error or no result.
    """
    try:
        time.sleep(anilist_client.REQUEST_DELAY)
        if cached_id:
            return anilist_client.get_manga(cached_id)
        return anilist_client.search_manga(title)
    except Exception:  # noqa: BLE001
        _log.warning("AniList lookup failed for %r", title, exc_info=True)
        return None


def _apply_progress(entry: MangaEntry, progress: dict) -> None:
    entry.scan_latest_chapter = progress.get("scan_latest_chapter")
    entry.publisher_name = progress.get("publisher_name")
    entry.publisher_chapters = progress.get("publisher_chapters")
    entry.publisher_volumes = progress.get("publisher_volumes")
    entry.publisher_status = progress.get("publisher_status")
    if "scan_latest_volume" in progress:
        entry.scan_latest_volume = progress.get("scan_latest_volume")
    if "anilist_id" in progress:
        entry.anilist_id = progress.get("anilist_id")
    if "anilist_chapters" in progress:
        entry.anilist_chapters = progress.get("anilist_chapters")
    if "anilist_volumes" in progress:
        entry.anilist_volumes = progress.get("anilist_volumes")
    if "completed_in_origin" in progress:
        entry.completed_in_origin = progress.get("completed_in_origin")
