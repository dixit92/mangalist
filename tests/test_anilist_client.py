"""``anilist_client`` on synthetic HTTP answers (no network): the real status reaches the log, a rate limit
is retried once after its Retry-After, a 404 is "no match", and every request carries MangaList's
User-Agent (AniList's Cloudflare front blocks generic client signatures)."""

from __future__ import annotations

import json
import logging

import pytest
import requests

from mangalist import anilist_client, mu_client
from mangalist.http_identity import USER_AGENT

BERSERK = {"data": {"Media": {"id": 30002, "title": {"romaji": "Berserk", "english": "Berserk"},
                              "chapters": None, "volumes": None}}}


def response(status: int, body, headers=None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(body).encode() if not isinstance(body, bytes) else body
    r.headers.update(headers or {})
    return r


@pytest.fixture
def answers(monkeypatch):
    queue: list = []
    sent: list = []
    sleeps: list = []

    def post(url, json=None, timeout=None):
        sent.append(json)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(anilist_client._SESSION, "post", post)
    monkeypatch.setattr(anilist_client.time, "sleep", sleeps.append)
    return queue, sent, sleeps


def test_both_clients_send_the_mangalist_user_agent():
    assert USER_AGENT.startswith("MangaList/") and "github.com/dixit92/MangaList" in USER_AGENT
    assert anilist_client._SESSION.headers["User-Agent"] == USER_AGENT
    assert mu_client._SESSION.headers["User-Agent"] == USER_AGENT


def test_lookup_by_id(answers):
    queue, sent, _ = answers
    queue.append(response(200, BERSERK))
    assert anilist_client.get_manga(30002) == {"id": 30002, "title": "Berserk", "english_title": "Berserk",
                                               "chapters": None, "volumes": None, "external_links": []}
    assert sent[0]["variables"] == {"id": 30002}


def test_a_rate_limit_is_retried_once_after_its_retry_after(answers):
    queue, sent, sleeps = answers
    queue += [response(429, {"errors": [{"message": "Too Many Requests."}]}, {"Retry-After": "30"}), response(200, BERSERK)]
    assert anilist_client.get_manga(30002)["id"] == 30002
    assert len(sent) == 2 and sleeps == [30.0]


def test_a_long_retry_after_is_capped(answers):
    queue, _, sleeps = answers
    queue += [response(429, {}, {"Retry-After": "600"}), response(200, BERSERK)]
    anilist_client.get_manga(30002)
    assert sleeps == [anilist_client.MAX_RETRY_AFTER]


def test_the_real_status_and_reason_are_logged(answers, caplog):
    # The old client logged every HTTP error as "HTTP 0": an error Response is falsy.
    queue, sent, sleeps = answers
    queue.append(response(403, {"title": "Error 1010: Access denied", "error_code": 1010}))
    with caplog.at_level(logging.WARNING, logger=anilist_client.__name__):
        assert anilist_client.search_manga("Berserk") is None
    assert "HTTP 403" in caplog.text and "Error 1010" in caplog.text
    assert len(sent) == 1 and sleeps == []  # a block is not retried


def test_a_404_is_no_match_and_not_retried(answers, caplog):
    queue, sent, _ = answers
    queue.append(response(404, {"errors": [{"message": "Not Found.", "status": 404}], "data": {"Media": None}}))
    with caplog.at_level(logging.WARNING, logger=anilist_client.__name__):
        assert anilist_client.search_manga("Zzqx Nonexistent") is None
    assert len(sent) == 1 and caplog.text == ""


def test_a_query_error_is_logged_with_its_message(answers, caplog):
    queue, _, _ = answers
    queue.append(response(400, {"errors": [{"message": "Variable \"$id\" got invalid value"}], "data": None}))
    with caplog.at_level(logging.WARNING, logger=anilist_client.__name__):
        assert anilist_client.get_manga(1) is None
    assert "HTTP 400" in caplog.text and "invalid value" in caplog.text


def test_a_network_error_is_logged_and_ends_the_lookup(answers, caplog):
    queue, sent, _ = answers
    queue.append(requests.ConnectionError("offline"))
    with caplog.at_level(logging.WARNING, logger=anilist_client.__name__):
        assert anilist_client.get_manga(1) is None
    assert "ConnectionError" in caplog.text and len(sent) == 1


def test_the_delay_keeps_under_thirty_requests_a_minute():
    assert 60.0 / anilist_client.REQUEST_DELAY < 30
