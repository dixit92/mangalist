from __future__ import annotations

import pytest
import requests

from mangalist.services.nyaa import NyaaClient, RateLimited, UnexpectedResponse, Unreachable

from .conftest import FakeGet, FakeResponse, feed, item


def make(get, clock, **kw):
    return NyaaClient(get=get, sleep=clock.sleep, clock=clock, **kw)


def ok():
    return FakeResponse(200, feed(item("Berserk v01 (Digital)")))


def test_request_shape(clock):
    get = FakeGet(ok())
    items = make(get, clock).rss("berserk digital")
    assert [i.title for i in items] == ["Berserk v01 (Digital)"]
    call = get.calls[0]
    assert call["url"] == "https://nyaa.si/"
    assert call["params"] == {"page": "rss", "q": "berserk digital", "c": "3_1", "f": "0"}
    assert call["timeout"] == 20.0


def test_the_category_is_a_parameter(clock):
    get = FakeGet(ok())
    make(get, clock).rss("x", "3_3")
    assert get.calls[0]["params"]["c"] == "3_3"


def test_at_least_two_seconds_between_requests(clock):
    c = make(FakeGet(ok()), clock)
    c.rss("a")
    assert clock.slept == []                        # the first request does not wait
    c.rss("b")
    assert clock.slept == [2.0]
    clock.now += 10                                 # a long pause already satisfies the interval
    c.rss("c")
    assert clock.slept == [2.0]


def test_the_interval_is_configurable(clock):
    c = make(FakeGet(ok()), clock, min_interval=5)
    c.rss("a")
    c.rss("b")
    assert clock.slept == [5.0]


def test_429_waits_for_retry_after_then_succeeds(clock):
    get = FakeGet(FakeResponse(429, headers={"Retry-After": "7"}), ok())
    assert len(make(get, clock).rss("a")) == 1
    assert 7.0 in clock.slept and len(get.calls) == 2


def test_429_with_a_long_retry_after_is_raised_at_once(clock):
    get = FakeGet(FakeResponse(429, headers={"Retry-After": "600"}))
    with pytest.raises(RateLimited) as err:
        make(get, clock).rss("a")
    assert err.value.retry_after == 600.0 and len(get.calls) == 1


def test_429_without_retry_after_is_raised(clock):
    with pytest.raises(RateLimited):
        make(FakeGet(FakeResponse(429)), clock).rss("a")


def test_429_gives_up_after_max_retries(clock):
    get = FakeGet(FakeResponse(429, headers={"Retry-After": "1"}))
    with pytest.raises(RateLimited):
        make(get, clock, max_retries=2).rss("a")
    assert len(get.calls) == 3


def test_5xx_is_retried_then_succeeds(clock):
    get = FakeGet(FakeResponse(503), FakeResponse(502), ok())
    assert len(make(get, clock).rss("a")) == 1
    assert len(get.calls) == 3


def test_persistent_5xx_is_unexpected_response(clock):
    get = FakeGet(FakeResponse(503))
    with pytest.raises(UnexpectedResponse) as err:
        make(get, clock, max_retries=1).rss("a")
    assert err.value.status == 503 and len(get.calls) == 2


def test_4xx_is_not_retried(clock):
    get = FakeGet(FakeResponse(403))
    with pytest.raises(UnexpectedResponse) as err:
        make(get, clock).rss("a")
    assert err.value.status == 403 and len(get.calls) == 1


def test_timeout_is_retried_then_unreachable(clock):
    get = FakeGet(requests.exceptions.ReadTimeout("slow"))
    with pytest.raises(Unreachable):
        make(get, clock, max_retries=1).rss("a")
    assert len(get.calls) == 2


def test_connection_error_is_unreachable_at_once(clock):
    get = FakeGet(requests.exceptions.ConnectionError("refused"))
    with pytest.raises(Unreachable) as err:
        make(get, clock).rss("a")
    assert len(get.calls) == 1 and "ConnectionError" in str(err.value)


def test_tls_error_is_unreachable(clock):
    with pytest.raises(Unreachable):
        make(FakeGet(requests.exceptions.SSLError("bad cert")), clock).rss("a")


def test_an_html_page_is_not_a_feed(clock):
    get = FakeGet(FakeResponse(200, b"<html><title>Cloudflare</title></html>"))
    with pytest.raises(UnexpectedResponse):
        make(get, clock).rss("a")


def test_an_oversized_answer_is_refused(clock):
    get = FakeGet(FakeResponse(200, b"x" * (9 * 1024 * 1024)))
    with pytest.raises(UnexpectedResponse):
        make(get, clock).rss("a")


def test_the_default_session_identifies_the_program_and_ignores_the_environment():
    from mangalist.http_identity import USER_AGENT

    c = NyaaClient()
    try:
        assert c._session.headers["User-Agent"] == USER_AGENT
        assert c._session.trust_env is False
    finally:
        c.close()


def test_a_search_logs_its_outcome_at_info_and_a_failure_at_warning(clock, caplog):
    import logging

    caplog.set_level(logging.DEBUG)
    make(FakeGet(ok()), clock).rss("berserk digital")
    info = [r for r in caplog.records if r.levelno == logging.INFO and "searched" in r.getMessage()]
    assert len(info) == 1 and "'berserk digital'" in info[0].getMessage() and "1 releases" in info[0].getMessage()
    caplog.clear()
    with pytest.raises(UnexpectedResponse):
        make(FakeGet(FakeResponse(403)), clock).rss("berserk")
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "search for 'berserk' failed" in warned[0].getMessage()
