"""NyaaClient.torrent: a release's .torrent fetched politely, only from nyaa's own host."""

from __future__ import annotations

import pytest
import requests

from mangalist.services.nyaa import NyaaClient, RateLimited, UnexpectedResponse, Unreachable

from .conftest import FakeGet, FakeResponse, feed, item

LINK = "https://nyaa.si/download/123456.torrent"
TORRENT = b"d4:infod4:name1:a6:lengthi5eee"


def make(get, clock, **kw):
    return NyaaClient(get=get, sleep=clock.sleep, clock=clock, **kw)


def test_the_torrent_is_fetched_from_its_link_with_no_query(clock):
    get = FakeGet(FakeResponse(200, TORRENT))
    assert make(get, clock).torrent(LINK) == TORRENT
    assert get.calls == [{"url": LINK, "params": {}, "timeout": 20.0}]


def test_it_keeps_the_politeness_pause_shared_with_the_search(clock):
    get = FakeGet(FakeResponse(200, feed(item("Berserk v01 (Digital)"))), FakeResponse(200, TORRENT))
    c = make(get, clock)
    c.rss("berserk")
    assert clock.slept == []
    c.torrent(LINK)
    assert clock.slept == [2.0]                                  # the torrent waits for the search's two seconds
    c.torrent(LINK)
    assert clock.slept == [2.0, 2.0]


@pytest.mark.parametrize("link", [
    "https://evil.example/download/1.torrent", "http://nyaa.si.evil.example/download/1.torrent",
    "https://user:pw@nyaa.si/download/1.torrent", "ftp://nyaa.si/download/1.torrent", "magnet:?xt=urn:btih:" + "a" * 40,
    "", "   ", "/download/1.torrent", "https://nyaa.si/download/1.torrent\nhttps://evil.example/x",
    "https://nyaa.si/down load/1.torrent"])
def test_only_nyaas_own_links_are_fetched(clock, link):
    get = FakeGet(FakeResponse(200, TORRENT))
    with pytest.raises(UnexpectedResponse, match="not on nyaa"):
        make(get, clock).torrent(link)
    assert get.calls == []                                       # nothing was sent anywhere


def test_another_nyaa_base_url_is_its_own_host(clock):
    get = FakeGet(FakeResponse(200, TORRENT))
    c = NyaaClient(
        "https://nyaa.example/", get=get, sleep=clock.sleep, clock=clock)
    assert c.torrent("https://nyaa.example/download/9.torrent") == TORRENT
    with pytest.raises(UnexpectedResponse):
        c.torrent(LINK)


def test_a_block_page_is_not_a_torrent(clock):
    with pytest.raises(UnexpectedResponse, match="torrent file"):
        make(FakeGet(FakeResponse(200, b"<html>Just a moment...</html>")), clock).torrent(LINK)


def test_a_missing_torrent_is_a_refusal(clock):
    with pytest.raises(UnexpectedResponse) as err:
        make(FakeGet(FakeResponse(404)), clock).torrent(LINK)
    assert err.value.status == 404


def test_a_server_error_is_retried_like_the_search(clock):
    get = FakeGet(FakeResponse(503), FakeResponse(200, TORRENT))
    assert make(get, clock).torrent(LINK) == TORRENT
    assert len(get.calls) == 2 and clock.slept == [4.0]


def test_a_rate_limit_waits_for_retry_after_or_gives_up(clock):
    get = FakeGet(FakeResponse(429, headers={"Retry-After": "5"}), FakeResponse(200, TORRENT))
    assert make(get, clock).torrent(LINK) == TORRENT and clock.slept == [5.0]
    with pytest.raises(RateLimited):
        make(FakeGet(FakeResponse(429, headers={"Retry-After": "600"})), clock).torrent(LINK)


def test_a_timeout_is_unreachable(clock):
    with pytest.raises(Unreachable):
        make(FakeGet(requests.exceptions.ConnectionError("boom")), clock).torrent(LINK)


def test_an_oversized_answer_is_ignored(clock):
    with pytest.raises(UnexpectedResponse, match="large"):
        make(FakeGet(FakeResponse(200, b"d" + b"0" * (9 * 1024 * 1024))), clock).torrent(LINK)


def test_the_real_session_asks_for_a_torrent_not_the_feed(clock, monkeypatch):
    c = NyaaClient(sleep=clock.sleep, clock=clock)
    seen = []

    class Resp:
        status_code, content, headers = 200, TORRENT, {}

    monkeypatch.setattr(c._session, "get", lambda url, **kw: seen.append((url, kw.get("headers"))) or Resp())
    c.torrent(LINK)
    with pytest.raises(UnexpectedResponse):                    # the fake answers a torrent: not a feed
        c.rss("berserk")
    assert seen[0][1] == {"Accept": "application/x-bittorrent, */*"} and seen[1][1] is None
    c.close()
