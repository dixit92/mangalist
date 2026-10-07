"""Nyaa's RSS feed (``https://nyaa.si/?page=rss``) -> :class:`RssItem`. Pure parsing, no I/O, no Qt.

One ``<item>`` carries the release title, the ``.torrent`` link, the view page (``guid``), the publish date and
the ``nyaa:`` namespace fields (seeders, leechers, downloads, infoHash, categoryId, size, trusted, remake).
Every field but the title and the info hash is optional: a missing or malformed one becomes its neutral value
(0, ``""``, ``False``), never an exception. An item without an info hash is skipped (MangaList tracks a torrent
by it).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import List, Optional

NS = "{https://nyaa.si/xmlns/nyaa}"

_HASH = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SIZE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([KMGT]?)(i?)B\s*$", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


class FeedError(ValueError):
    """The document is not a nyaa RSS feed (not XML, a DTD / entity declaration, no ``<channel>``)."""


@dataclass(frozen=True)
class RssItem:
    title: str
    torrent_url: str
    view_url: str
    info_hash: str                      # lowercase hex
    published: str                      # ISO 8601 UTC ('' when the date is missing or unreadable)
    seeders: int = 0
    leechers: int = 0
    downloads: int = 0
    size_bytes: int = 0
    category: str = ""                  # '3_1'
    trusted: bool = False
    remake: bool = False


def parse_size(text: Optional[str]) -> int:
    """``"1.2 GiB"`` -> bytes (binary units; ``GB`` etc. are read as binary too, as nyaa does); 0 when unreadable."""
    m = _SIZE.match(text or "")
    if m is None:
        return 0
    try:
        number = Decimal(m.group(1).replace(",", "."))
    except InvalidOperation:
        return 0
    unit = m.group(2).upper()
    return int(number * _UNITS[unit])


def parse_pubdate(text: Optional[str]) -> str:
    """RFC 822 date (``Mon, 03 Aug 2026 19:48:36 -0000``) -> ISO 8601 UTC; '' when unreadable. A date without
    a usable zone (nyaa writes ``-0000``) is UTC."""
    if not text or not text.strip():
        return ""
    try:
        when = parsedate_to_datetime(text.strip())
    except (TypeError, ValueError, IndexError):
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def _int(text: str) -> int:
    try:
        return max(0, int(text))
    except ValueError:
        return 0


def _yes(text: str) -> bool:
    return text.strip().lower() in ("yes", "true", "1")


def parse_feed(content: bytes) -> List[RssItem]:
    """The items of one RSS document, in feed order. Raises :class:`FeedError` when it is not a feed."""
    head = content[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in content[:65536].lower():
        # No document type or entity declarations in a feed: refuse them (entity-expansion attacks).
        raise FeedError("the feed declares a DTD / entities; refused")
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise FeedError("the answer is not valid XML") from None
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FeedError("the answer is not an RSS feed")
    items: List[RssItem] = []
    for node in channel.findall("item"):
        title = _text(node, "title")
        info_hash = _text(node, NS + "infoHash").lower()
        if not title or not _HASH.match(info_hash):
            continue
        items.append(RssItem(
            title=title,
            torrent_url=_text(node, "link"),
            view_url=_text(node, "guid"),
            info_hash=info_hash,
            published=parse_pubdate(_text(node, "pubDate")),
            seeders=_int(_text(node, NS + "seeders")),
            leechers=_int(_text(node, NS + "leechers")),
            downloads=_int(_text(node, NS + "downloads")),
            size_bytes=parse_size(_text(node, NS + "size")),
            category=_text(node, NS + "categoryId"),
            trusted=_yes(_text(node, NS + "trusted")),
            remake=_yes(_text(node, NS + "remake")),
        ))
    return items


__all__ = ["FeedError", "RssItem", "parse_feed", "parse_pubdate", "parse_size"]
