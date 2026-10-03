"""Official sources (A12): MangaPixer links, else AniList's, MangaUpdates publisher names, store searches;
ordering and de-duplication. AniList externalLinks parsing on recorded synthetic answers (no network)."""

from __future__ import annotations

import json

import requests

from mangalist import anilist_client
from mangalist.knowledge import EnglishPublisher, OfficialLink, from_mangapixer_item
from mangalist.official_sources import (
    anilist_links,
    is_web_url,
    official_links,
    order_and_dedup,
    primary_label,
    store_search_links,
)

from .helpers import item, own

AL_ROWS = [
    {"url": "https://twitter.example.com/examplesaga", "site": "Twitter", "type": "SOCIAL", "language": None},
    {"url": "https://reader.example.com/saga", "site": "Example Reader", "type": "STREAMING", "language": "English"},
    {"url": "https://raw.example.jp/saga", "site": "Raw Reader", "type": "STREAMING", "language": "Japanese"},
    {"url": "https://press.example.com/saga/", "site": "Example Press", "type": "INFO", "language": "English"},
    {"url": "", "site": "Broken", "type": "INFO", "language": "English"},
]


def test_store_searches_are_constructed_and_labelled():
    links = store_search_links("Example Saga: Part 2")
    assert [link.label for link in links] == ["Amazon (search)", "BookWalker Global (search)", "Kobo (search)"]
    assert links[0].url == "https://www.amazon.com/s?k=Example+Saga%3A+Part+2&i=stripbooks"
    assert links[1].url == "https://global.bookwalker.jp/search/?word=Example+Saga%3A+Part+2"
    assert links[2].url == "https://www.kobo.com/search?query=Example+Saga%3A+Part+2"
    assert {link.kind for link in links} == {"search"} and {link.source for link in links} == {"search"}
    assert store_search_links("") == [] and store_search_links(None) == []


def test_mangapixer_links_first_then_publisher_names_then_searches():
    links = official_links(from_mangapixer_item(item()))
    assert [(link.kind, link.label) for link in links] == [
        ("publisher", "Official (original language)"),
        ("publisher", "Official English release"),
        ("publisher", "Example Press (English publisher)"),
        ("store", "BookWalker"),
        ("search", "Amazon (search)"), ("search", "BookWalker Global (search)"), ("search", "Kobo (search)"),
    ]
    assert links[2].url is None and links[2].source == "mangaupdates"
    assert "Example+Quest" in links[4].url


def test_anilist_links_when_mangapixer_has_none():
    k = own(english_title="Example Saga EN", anilist_links=tuple(AL_ROWS),
            english_publishers=(EnglishPublisher("Example Press"),))
    links = official_links(k)
    assert [(link.kind, link.label, link.source) for link in links][:2] == [
        ("publisher", "Example Press", "anilist"), ("reader", "Example Reader", "anilist")]
    # The MangaUpdates publisher name is already carried by AniList's link: not repeated.
    assert not any(link.source == "mangaupdates" for link in links)
    assert "Example+Saga+EN" in links[-1].url
    # Social accounts and other languages are left out.
    assert all("twitter" not in (link.url or "") and "raw." not in (link.url or "") for link in links)


def test_mangapixer_links_win_over_anilist():
    k = from_mangapixer_item(item())
    k = k.__class__(**{**k.__dict__, "anilist_links": tuple(AL_ROWS)})
    assert not any(link.source == "anilist" for link in official_links(k))
    k = from_mangapixer_item(item(officialLinks=[]))
    links = official_links(k, anilist_rows=AL_ROWS)
    assert [link.source for link in links][:2] == ["anilist", "anilist"]


def test_dedup_by_url_and_order_by_kind():
    links = [
        OfficialLink("search", "Kobo (search)", "https://www.kobo.com/search?query=x", "search"),
        OfficialLink("store", "Store", "https://store.example.com/x/", "mangadex"),
        OfficialLink("publisher", "Press", "https://www.press.example.com/x", "mangadex"),
        OfficialLink("store", "Store again", "http://store.example.com/x", "anilist"),
        OfficialLink("publisher", "Press (English publisher)", None, "mangaupdates"),
        OfficialLink("publisher", "press (english publisher)", None, "mangaupdates"),
        OfficialLink("reader", "Reader", "https://reader.example.com/x", "anilist"),
        OfficialLink("publisher", "Press dup", "https://press.example.com/x/", "anilist"),
    ]
    got = order_and_dedup(links)
    assert [link.label for link in got] == ["Press", "Press (English publisher)", "Reader", "Store", "Kobo (search)"]


def test_nothing_known_still_lists_searches_by_title():
    assert [link.kind for link in official_links(None, title="Example Saga")] == ["search"] * 3
    assert official_links(None) == []


def test_primary_label():
    assert primary_label(official_links(from_mangapixer_item(item()))) == "Official (original language)"
    k = own(english_publishers=(EnglishPublisher("Example Press"),))
    assert primary_label(official_links(k)) == "Example Press"
    assert primary_label(official_links(own())) == "Search only"
    assert primary_label([]) == ""


def test_anilist_links_parsing_rules():
    got = anilist_links(AL_ROWS)
    assert [(link.kind, link.label, link.url) for link in got] == [
        ("reader", "Example Reader", "https://reader.example.com/saga"),
        ("publisher", "Example Press", "https://press.example.com/saga/")]


# --- anilist_client: externalLinks in the same request -------------------------------------------


def _response(body) -> requests.Response:
    r = requests.Response()
    r.status_code = 200
    r._content = json.dumps(body).encode()
    return r


RECORDED = {"data": {"Media": {
    "id": 900002, "title": {"romaji": "Reigai Saga", "english": "Example Saga"}, "chapters": 98, "volumes": 12,
    "externalLinks": AL_ROWS + [{"site": "No URL", "type": "INFO"}, "not a dict"],
}}}


def test_the_query_asks_for_external_links_and_parses_them(monkeypatch):
    sent = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        return _response(RECORDED)

    monkeypatch.setattr(anilist_client._SESSION, "post", post)
    monkeypatch.setattr(anilist_client.time, "sleep", lambda s: None)
    got = anilist_client.get_manga(900002)
    assert "externalLinks { url site type language }" in sent[0]["query"]
    assert got["english_title"] == "Example Saga" and got["volumes"] == 12
    assert [row["site"] for row in got["external_links"]] == [
        "Twitter", "Example Reader", "Raw Reader", "Example Press"]
    assert len(sent) == 1                                   # one request, no extra calls
    got = anilist_client.search_manga("Example Saga")
    assert "externalLinks { url site type language }" in sent[1]["query"] and got["id"] == 900002


def test_media_without_external_links():
    assert anilist_client.parse_external_links({}) == []
    assert anilist_client.parse_external_links({"externalLinks": None}) == []


def test_only_web_links_survive():
    # The URLs come from AniList / MangaPixer data and are opened in the browser.
    links = [
        OfficialLink("publisher", "Script", "javascript:alert(1)", "anilist"),
        OfficialLink("reader", "Local", "file:///etc/passwd", "mangapixer"),
        OfficialLink("store", "No host", "https://", "anilist"),
        OfficialLink("store", "Shop", "https://shop.example/series", "anilist"),
        OfficialLink("publisher", "Name only", None, "mangaupdates"),
    ]
    kept = order_and_dedup(links)
    assert [link.label for link in kept] == ["Name only", "Shop"]
    assert is_web_url("HTTP://Example.com/x") and not is_web_url("mailto:a@b.c")
