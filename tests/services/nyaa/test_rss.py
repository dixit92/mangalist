from __future__ import annotations

import pytest

from mangalist.services.nyaa import FeedError, parse_feed
from mangalist.services.nyaa.rss import parse_pubdate, parse_size

from .conftest import feed, fixture_bytes, info_hash, item


@pytest.mark.parametrize("text,expected", [
    ("1.2 GiB", int(1.2 * 1024 ** 3)),
    ("107.8 MiB", int(107.8 * 1024 ** 2)),
    ("10 KiB", 10 * 1024),
    ("1 TiB", 1024 ** 4),
    ("512 B", 512),
    ("3.7 GB", int(3.7 * 1024 ** 3)),
    ("  2,5 MiB ", int(2.5 * 1024 ** 2)),
    ("", 0), (None, 0), ("lots", 0), ("1.2 parsecs", 0),
])
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_pubdate_with_nyaas_unknown_zone_is_utc():
    assert parse_pubdate("Mon, 03 Aug 2026 19:48:36 -0000") == "2026-08-03T19:48:36+00:00"


def test_pubdate_with_an_offset_is_converted_to_utc():
    assert parse_pubdate("Mon, 03 Aug 2026 21:48:36 +0200") == "2026-08-03T19:48:36+00:00"


@pytest.mark.parametrize("text", ["", None, "yesterday", "Mon, 99 Foo 2026 99:99:99 -0000"])
def test_unreadable_pubdate_is_empty(text):
    assert parse_pubdate(text) == ""


def test_item_fields():
    xml = feed(item("Berserk v42 (2025) (Digital) (LuCaZ)", seeders=100, trusted=True, remake=False,
                    size="346.7 MiB", leechers=3, downloads=77, category="3_1", hash=info_hash("a")))
    (it,) = parse_feed(xml)
    assert it.title == "Berserk v42 (2025) (Digital) (LuCaZ)"
    assert it.info_hash == info_hash("a")            # lower-cased
    assert it.torrent_url.endswith(".torrent") and it.view_url.startswith("https://nyaa.si/view/")
    assert (it.seeders, it.leechers, it.downloads) == (100, 3, 77)
    assert it.size_bytes == int(346.7 * 1024 ** 2)
    assert (it.trusted, it.remake, it.category) == (True, False, "3_1")
    assert it.published == "2026-08-03T19:48:36+00:00"


def test_missing_optional_fields_are_neutral():
    minimal = f"""<item><title>Some Title v01</title><nyaa:infoHash>{info_hash('m')}</nyaa:infoHash></item>"""
    (it,) = parse_feed(feed(minimal))
    assert (it.seeders, it.leechers, it.downloads, it.size_bytes) == (0, 0, 0, 0)
    assert (it.torrent_url, it.view_url, it.published, it.category) == ("", "", "", "")
    assert (it.trusted, it.remake) == (False, False)


def test_garbage_numbers_do_not_raise():
    bad = f"""<item><title>T v1</title><nyaa:infoHash>{info_hash('g')}</nyaa:infoHash>
<nyaa:seeders>many</nyaa:seeders><nyaa:leechers>-4</nyaa:leechers><nyaa:size>??</nyaa:size></item>"""
    (it,) = parse_feed(feed(bad))
    assert (it.seeders, it.leechers, it.size_bytes) == (0, 0, 0)


def test_items_without_title_or_a_real_info_hash_are_skipped():
    items = [
        "<item><title>No hash</title></item>",
        f"<item><nyaa:infoHash>{info_hash('x')}</nyaa:infoHash></item>",
        "<item><title>Short hash</title><nyaa:infoHash>abc123</nyaa:infoHash></item>",
        item("Kept v01", hash=info_hash("kept")),
    ]
    assert [i.title for i in parse_feed(feed(*items))] == ["Kept v01"]


def test_an_empty_feed_is_an_empty_list():
    assert parse_feed(feed()) == []


@pytest.mark.parametrize("content", [b"", b"<html><body>Maintenance</body></html>", b"not xml at all", b"<rss/>"])
def test_not_a_feed(content):
    with pytest.raises(FeedError):
        parse_feed(content)


def test_a_dtd_with_entities_is_refused():
    evil = b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss><channel><title>&a;</title></channel></rss>'
    with pytest.raises(FeedError):
        parse_feed(evil)


@pytest.mark.parametrize("name,count", [("berserk.xml", 14), ("vinland-saga.xml", 17), ("overlord.xml", 23)])
def test_recorded_feeds_parse(name, count):
    items = parse_feed(fixture_bytes(name))
    assert len(items) == count
    assert all(len(i.info_hash) == 40 and i.info_hash == i.info_hash.lower() for i in items)
    assert all(i.category == "3_1" and i.size_bytes > 0 and i.published.endswith("+00:00") for i in items)
