"""The MangaPixer client against a local fake server: headers, paging, include, errors, the token rules."""

from __future__ import annotations

import logging
import socket

import pytest

from mangalist.http_identity import USER_AGENT
from mangalist.services.mangapixer import client as mpc

from .conftest import TOKEN, folder, load_fixture


def test_ping_sends_bearer_and_user_agent_and_never_the_token_in_the_url(fake, client_factory):
    c = client_factory(fake.url)
    ping = c.ping()
    assert ping.ok and ping.server_time == fake.server_time and ping.auth == "token"
    req = fake.requests[-1]
    assert req["path"] == "/api/v1/export/ping"
    assert req["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert req["headers"]["user-agent"] == USER_AGENT
    assert TOKEN not in req["path"] and all(TOKEN not in v for v in req["query"].values())
    assert TOKEN not in repr(c) and TOKEN not in str(vars(c))


@pytest.mark.parametrize("given, expected", [
    ("192.168.1.10:8080", "http://192.168.1.10:8080"),
    ("http://nas.local:8080/", "http://nas.local:8080"),
    ("HTTPS://mp.example.org/mangapixer/api/v1/export/", "https://mp.example.org/mangapixer"),
])
def test_base_url_normalised(given, expected):
    assert mpc.normalize_base_url(given) == expected


@pytest.mark.parametrize("bad", ["", "ftp://x", "http://user:pw@host", "http://host/?token=x", "http://"])
def test_base_url_refused(bad):
    with pytest.raises(ValueError):
        mpc.normalize_base_url(bad)


def test_libraries_ignore_unknown_fields(fake, client_factory):
    fake.libraries.append({"id": "lib1", "displayName": "Comics", "kind": "comic", "folderCount": "3",
                           "itemCount": None, "lastScanAt": None, "brandNew": [1, 2]})
    libs = client_factory(fake.url).libraries()
    assert [(lib.id, lib.kind, lib.folder_count) for lib in libs] == [("lib0manga", "manga", 10), ("lib1", "comic", 3)]


def test_export_pages_follow_next_cursor(fake, client_factory):
    fake.items["lib0manga"] = [folder(f"n{i:04d}", ["S", f"Series {i}"]) for i in range(7)]
    pages = list(client_factory(fake.url).export_pages("lib0manga", limit=3))
    assert [len(p.items) for p in pages] == [3, 3, 1]
    assert [p.first for p in pages] == [True, False, False]
    assert [r["query"].get("cursor") for r in fake.requests] == [None, "3", "6"]
    assert all(r["query"]["limit"] == "3" and r["query"]["library"] == "lib0manga" for r in fake.requests)
    assert pages[-1].next_cursor is None


def test_include_parameter(fake, client_factory):
    fake.items["lib0manga"] = load_fixture()["items"]
    c = client_factory(fake.url)
    list(c.export_pages("lib0manga"))
    assert "include" not in fake.requests[-1]["query"]                   # MangaPixer's default: all blocks
    page = next(c.export_pages("lib0manga", include=["volumes"]))
    assert fake.requests[-1]["query"]["include"] == "volumes"
    assert page.items[0]["volumes"] is not None and page.items[0]["completion"] is None
    next(c.export_pages("lib0manga", include=[]))
    assert fake.requests[-1]["query"]["include"] == ""                    # include= : none
    with pytest.raises(ValueError):
        next(c.export_pages("lib0manga", include=["paths"]))
    with pytest.raises(ValueError):
        next(c.export_pages("lib0manga", limit=501))


def test_updated_since_is_sent(fake, client_factory):
    next(client_factory(fake.url).export_pages("lib0manga", updated_since="2026-10-01T00:00:00.000Z"))
    assert fake.requests[-1]["query"]["updatedSince"] == "2026-10-01T00:00:00.000Z"


def test_unknown_fields_pass_through(fake, client_factory):
    fake.extra_fields = True
    fake.items["lib0manga"] = [folder("n0001", ["A"])]
    page = next(client_factory(fake.url).export_pages("lib0manga"))
    assert page.items[0]["somethingNew"] == {"x": 1}


def test_schema_version_2_refused(fake, client_factory):
    fake.schema_version = 2
    c = client_factory(fake.url)
    with pytest.raises(mpc.UnsupportedSchema, match="version 2"):
        next(c.export_pages("lib0manga"))
    with pytest.raises(mpc.UnsupportedSchema):
        c.libraries()


def test_401_is_never_retried(fake, client_factory):
    c = client_factory(fake.url, token="mpx_wrong")
    with pytest.raises(mpc.TokenRejected) as err:
        c.ping()
    assert err.value.status == 401
    assert fake.count("ping") == 1
    with pytest.raises(mpc.TokenRejected):
        list(c.export_pages("lib0manga"))
    assert fake.count("metadata") == 1


def test_missing_token_sends_nothing(fake, client_factory):
    with pytest.raises(mpc.TokenRejected):
        client_factory(fake.url, token="").ping()
    with pytest.raises(mpc.TokenRejected):
        client_factory(fake.url, token="mpx_a\r\nX-Evil: 1").ping()
    assert fake.requests == []


def test_403_404_409_400(fake, client_factory):
    c = client_factory(fake.url)
    fake.queue.append(("ping", 403, None, {}))
    with pytest.raises(mpc.Forbidden):
        c.ping()
    with pytest.raises(mpc.LibraryNotFound):
        next(c.export_pages("nope"))
    fake.queue.append(("ping", 404, b"<html>not found</html>", {}))
    with pytest.raises(mpc.NotFound, match="1.33.0") as err:
        c.ping()
    assert not isinstance(err.value, mpc.LibraryNotFound)
    fake.window_start = "2026-09-01T00:00:00.000Z"
    with pytest.raises(mpc.FullSyncRequired):
        next(c.export_pages("lib0manga", updated_since="2026-08-01T00:00:00.000Z"))
    fake.queue.append(("metadata", 400, {"error": "invalidCursor"}, {}))
    with pytest.raises(mpc.BadRequest) as bad:
        next(c.export_pages("lib0manga"))
    assert bad.value.code == "invalidCursor"
    fake.queue.append(("ping", 503, None, {}))
    with pytest.raises(mpc.ServerError):
        c.ping()
    fake.queue.append(("ping", 200, b"not json", {}))
    with pytest.raises(mpc.ServerError):
        c.ping()


def test_429_honours_retry_after(fake, client_factory, sleeps):
    fake.queue.append(("ping", 429, {"error": "rate_limited", "message": "slow down"}, {"Retry-After": "7"}))
    fake.queue.append(("ping", 429, {"error": "rate_limited", "message": "slow down"}, {"Retry-After": "2"}))
    assert client_factory(fake.url).ping().ok
    assert sleeps == [7.0, 2.0]
    assert fake.count("ping") == 3


def test_429_retries_are_bounded(fake, client_factory, sleeps):
    for _ in range(5):
        fake.queue.append(("ping", 429, {"error": "rate_limited"}, {"Retry-After": "1"}))
    with pytest.raises(mpc.RateLimited) as err:
        client_factory(fake.url, max_retries=2).ping()
    assert sleeps == [1.0, 1.0] and fake.count("ping") == 3
    assert err.value.retry_after == 1.0


def test_429_with_a_long_wait_is_not_waited_out(fake, client_factory, sleeps):
    fake.queue.append(("ping", 429, {"error": "too_many_attempts"}, {"Retry-After": "300"}))
    with pytest.raises(mpc.RateLimited, match="too many wrong tokens") as err:
        client_factory(fake.url).ping()
    assert sleeps == [] and err.value.code == "too_many_attempts" and fake.count("ping") == 1


def test_network_error_is_typed():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    c = mpc.MangaPixerClient(f"http://127.0.0.1:{port}", TOKEN, timeout=2)
    with pytest.raises(mpc.ConnectionFailed) as err:
        c.ping()
    assert TOKEN not in str(err.value) and err.value.__cause__ is None


def test_https_handshake_failure_is_a_connection_error(fake):
    """HTTPS to a plain-HTTP port fails the TLS handshake: a typed error, the token not in it."""
    c = mpc.MangaPixerClient(fake.url.replace("http://", "https://"), TOKEN, timeout=5)
    with pytest.raises(mpc.ConnectionFailed) as err:
        c.ping()
    assert TOKEN not in str(err.value)


def test_tls_options():
    assert mpc.MangaPixerClient("https://h", TOKEN).verify is True
    assert mpc.MangaPixerClient("https://h", TOKEN, verify="/etc/mp-ca.pem").verify == "/etc/mp-ca.pem"
    assert mpc.MangaPixerClient("https://h", TOKEN, verify=False).verify is False


def test_usable_record_ignores_unknown_providers():
    assert mpc.usable_record(folder("n1", ["A"], provider="mangaupdates"))["provider"] == "mangaupdates"
    assert mpc.usable_record(folder("n1", ["A"], provider="gcd")) is None
    assert mpc.usable_record(folder("n1", ["A"], state="NeedsReview")) is None


def test_token_never_in_logs(fake, client_factory, caplog, sleeps):
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger="urllib3")
    good = client_factory(fake.url)
    good.ping()
    fake.items["lib0manga"] = [folder(f"n{i:04d}", ["S", f"Series {i}"]) for i in range(3)]
    list(good.export_pages("lib0manga", limit=2))
    fake.queue.append(("ping", 429, {"error": "rate_limited"}, {"Retry-After": "1"}))
    good.ping()
    errors = []
    for exc_case in ("401", "404", "409"):
        try:
            if exc_case == "401":
                client_factory(fake.url, token=TOKEN + "x").ping()
            elif exc_case == "404":
                next(good.export_pages("nope"))
            else:
                fake.window_start = "2026-09-01"
                next(good.export_pages("lib0manga", updated_since="2026-01-01"))
        except mpc.MangaPixerError as exc:
            errors.append(f"{exc!s} {exc!r}")
    assert len(errors) == 3
    text = caplog.text + "\n".join(errors) + "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records, "expected some log output to check"
    assert TOKEN not in text and TOKEN[4:] not in text
