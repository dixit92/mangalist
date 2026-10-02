"""MangaUpdates id -> MangaDex record (UUID), for the hand-off targets that take a MangaDex link.

No downloader starts from a MangaUpdates id; MangaDex records carry ``attributes.links.mu``, the MangaUpdates
series id written in base36 (``njeqwry`` = 51239621230), so a title search on MangaDex plus that link identifies
the record exactly. A record is only accepted by that link - never by a title alone - so a hand-off never points at
the wrong series. Searches try the MangaUpdates title first, then its alternative titles (MangaDex lists many
series under the romaji title), at most :data:`MAX_SEARCHES` per series.

``get_json`` is injected: :func:`manga_list.mangadex_client.get_json` in the app, recorded answers in the tests.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

MAX_SEARCHES = 3
SITE = "https://mangadex.org"
# All ratings: the record is identified by its MangaUpdates link, not shown, so none may hide it.
CONTENT_RATINGS = ("safe", "suggestive", "erotica", "pornographic")

GetJson = Callable[[str, Dict[str, Any]], Dict[str, Any]]


def mu_slug(mu_id: int) -> str:
    """The base36 form MangaDex stores in ``links.mu`` (51239621230 -> ``njeqwry``)."""
    if mu_id <= 0:
        raise ValueError("MangaUpdates ids are positive")
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = ""
    n = mu_id
    while n:
        n, r = divmod(n, 36)
        out = digits[r] + out
    return out


def links_to(record: Dict[str, Any], mu_id: int) -> bool:
    """True when a MangaDex manga record's ``links.mu`` names ``mu_id`` (base36 slug, or the id itself)."""
    links = (record.get("attributes") or {}).get("links") or {}
    value = links.get("mu") if isinstance(links, dict) else None
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip().lower()
    return value == mu_slug(mu_id) or value == str(mu_id)


def resolve(mu_id: int, titles: Iterable[str], get_json: GetJson) -> Optional[str]:
    """The UUID of the MangaDex record that links to ``mu_id``, or None when no search found it."""
    tried = 0
    for title in titles:
        if not title or not title.strip():
            continue
        if tried == MAX_SEARCHES:
            break
        tried += 1
        data = get_json("/manga", {"title": title.strip(), "limit": 20, "contentRating[]": list(CONTENT_RATINGS)})
        for record in data.get("data") or ():
            if isinstance(record, dict) and record.get("id") and links_to(record, mu_id):
                return str(record["id"])
    return None


def title_url(uuid: str) -> str:
    """The MangaDex page of a series (what gallery-dl and FMD2's MangaDex module take)."""
    return f"{SITE}/title/{uuid}"
