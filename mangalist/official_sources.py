"""Official sources for a series (A12: always list the official source) - pure, no Qt, no requests.

Every series gets a list of :class:`~mangalist.knowledge.OfficialLink` (``kind`` publisher | reader |
store | search, ``label``, ``url``, ``source``), built in this order:

1. MangaPixer's ``officialLinks`` (from the linked MangaDex record) when MangaPixer has any;
2. otherwise AniList's external links: the English ones (``language`` English) of type INFO
   (-> publisher) and STREAMING (-> reader); social accounts and other languages are left out;
3. the English publishers MangaUpdates names (a name without a page: ``url`` None) unless a link
   above already carries that name;
4. always, store SEARCH links built from the English title (Amazon, BookWalker Global, Kobo) -
   constructed URLs, labelled "search", never fetched.

The list is ordered by kind (publisher, reader, store, search; the source order within a kind) and
de-duplicated by URL (scheme, ``www.`` and a trailing slash ignored) and, for links without a URL, by
label.
"""

from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import quote_plus, urlsplit

from .knowledge import OfficialLink, SeriesKnowledge

KIND_PUBLISHER = "publisher"
KIND_READER = "reader"
KIND_STORE = "store"
KIND_SEARCH = "search"
KIND_ORDER = (KIND_PUBLISHER, KIND_READER, KIND_STORE, KIND_SEARCH)

SOURCE_ANILIST = "anilist"
SOURCE_MANGAUPDATES = "mangaupdates"
SOURCE_SEARCH = "search"

# (label, URL template with {q} = the URL-encoded title)
STORE_SEARCHES = (
    ("Amazon (search)", "https://www.amazon.com/s?k={q}&i=stripbooks"),
    ("BookWalker Global (search)", "https://global.bookwalker.jp/search/?word={q}"),
    ("Kobo (search)", "https://www.kobo.com/search?query={q}"),
)

_ANILIST_KIND = {"INFO": KIND_PUBLISHER, "STREAMING": KIND_READER}


def store_search_links(title: Optional[str]) -> List[OfficialLink]:
    """The store search links for *title* (none without a title)."""
    title = (title or "").strip()
    if not title:
        return []
    q = quote_plus(title)
    return [OfficialLink(KIND_SEARCH, label, tpl.format(q=q), SOURCE_SEARCH) for label, tpl in STORE_SEARCHES]


def anilist_links(rows: Iterable[Mapping[str, Any]]) -> List[OfficialLink]:
    """Official links from AniList ``externalLinks`` rows (``{url, site, type, language}``)."""
    out: List[OfficialLink] = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        url = row.get("url")
        kind = _ANILIST_KIND.get(str(row.get("type") or "").upper())
        lang = str(row.get("language") or "").strip().lower()
        if not url or kind is None or lang != "english":
            continue
        out.append(OfficialLink(kind, str(row.get("site") or url), str(url), SOURCE_ANILIST))
    return out


def _url_key(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def order_and_dedup(links: Iterable[OfficialLink]) -> List[OfficialLink]:
    """Kind order (publisher, reader, store, search, then any other kind), stable within a kind; the
    first of two links to the same page (or two URL-less links with the same label) is kept."""
    seen = set()
    unique: List[OfficialLink] = []
    for link in links:
        key = ("url", _url_key(link.url)) if link.url else ("label", link.kind, link.label.strip().lower())
        if key in seen:
            continue
        seen.add(key)
        unique.append(link)

    def rank(link: OfficialLink) -> int:
        return KIND_ORDER.index(link.kind) if link.kind in KIND_ORDER else len(KIND_ORDER)

    return sorted(unique, key=rank)


def official_links(knowledge: Optional[SeriesKnowledge], *, anilist_rows: Optional[Sequence[Mapping]] = None,
                   title: Optional[str] = None) -> List[OfficialLink]:
    """The official sources of one series (see the module doc). *anilist_rows* overrides the
    knowledge's AniList rows; *title* overrides the title the store searches use (else the
    knowledge's English title, else its title)."""
    links: List[OfficialLink] = []
    if knowledge is not None and knowledge.official_links:
        links += list(knowledge.official_links)
    else:
        rows = anilist_rows if anilist_rows is not None else (knowledge.anilist_links if knowledge else ())
        links += anilist_links(rows)
    if knowledge is not None:
        labels = " ".join(link.label.lower() for link in links)
        for pub in knowledge.english_publishers:
            name = (pub.name or "").strip()
            if name and name.lower() not in labels:
                links.append(OfficialLink(KIND_PUBLISHER, f"{name} (English publisher)", None, SOURCE_MANGAUPDATES))
    search_title = title or (knowledge.search_title if knowledge is not None else None)
    links += store_search_links(search_title)
    return order_and_dedup(links)


def primary_label(links: Sequence[OfficialLink]) -> str:
    """Short text for a table cell: the first non-search source, else 'Search only'."""
    for link in links:
        if link.kind != KIND_SEARCH:
            name = link.label
            return name[: -len(" (English publisher)")] if name.endswith(" (English publisher)") else name
    return "Search only" if links else ""
