"""The password never shows: not in a repr, a log line, an exception, a URL, or any request but the login body."""

from __future__ import annotations

import logging

import pytest
import requests

from mangalist.downloads.contracts import QbtConnection
from mangalist.services.qbittorrent import (
    AuthFailed, DeleteRefused, IpBanned, QbtClient, QbtError, TorrentNotFound, TorrentRejected, UnexpectedResponse,
    Unreachable,
)

from .conftest import HASH_A, PASSWORD, USERNAME


def test_repr_and_str_never_show_the_password(conn, client):
    for obj in (conn, client, str(client), repr(vars(client)), repr(client._password), str(client._password)):
        assert PASSWORD not in repr(obj) and PASSWORD not in str(obj)
    assert "***" in repr(client)


def test_the_connection_dataclass_hides_it(conn):
    assert PASSWORD not in repr(conn)
    assert PASSWORD not in repr([conn]) and PASSWORD not in f"{conn}"


def test_nothing_is_logged_with_the_password(client, qbt, caplog):
    caplog.set_level(logging.DEBUG)
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    qbt.put_torrent(HASH_A)
    client.version()
    client.ensure_category("mangalist", "/data/x")
    client.add("https://nyaa.si/download/1.torrent", category="mangalist")
    client.torrents("mangalist")
    client.files(HASH_A)
    client.delete(HASH_A, delete_files=True)
    assert caplog.records
    assert PASSWORD not in caplog.text
    assert all(PASSWORD not in r.getMessage() and PASSWORD not in repr(r.args) for r in caplog.records)


def test_the_password_goes_only_into_the_login_body(client, qbt):
    qbt.put_torrent(HASH_A)
    client.version()
    client.ensure_category("mangalist", "/x")
    client.torrents("mangalist")
    client.delete(HASH_A, delete_files=True)
    for r in qbt.requests:
        carrier = repr((r["path"], r["query"], {k: v for k, v in r["headers"].items()}))
        assert PASSWORD not in carrier
        if r["path"] != "auth/login":
            assert PASSWORD not in repr(r["form"])
    assert qbt.calls("auth/login")[0]["form"]["password"] == PASSWORD


def test_no_error_shows_the_password(qbt, capsys):
    errors = []

    def capture(fn):
        try:
            fn()
        except (QbtError, ValueError) as exc:
            errors.append(exc)

    bad = QbtClient(QbtConnection(qbt.url, USERNAME, PASSWORD + "x"))
    capture(bad.version)
    qbt.banned = True
    capture(QbtClient(QbtConnection(qbt.url, USERNAME, PASSWORD)).version)
    qbt.banned = False
    gone = QbtClient(QbtConnection("http://127.0.0.1:9", USERNAME, PASSWORD), timeout=1)
    capture(gone.version)
    qbt.login_body_override = "<html>"
    capture(QbtClient(QbtConnection(qbt.url, USERNAME, PASSWORD)).version)
    qbt.login_body_override = None
    ok = QbtClient(QbtConnection(qbt.url, USERNAME, PASSWORD))
    qbt.add_answer = "Fails."
    capture(lambda: ok.add("magnet:?xt=urn:btih:" + HASH_A, category="mangalist"))
    capture(lambda: ok.files(HASH_A))
    capture(lambda: ok.delete(HASH_A, delete_files=True))
    qbt.put_torrent(HASH_A, category="other")
    capture(lambda: ok.delete(HASH_A, delete_files=True))
    capture(lambda: ok.add("nonsense", category="mangalist"))
    kinds = {type(e) for e in errors}
    assert {AuthFailed, IpBanned, Unreachable, UnexpectedResponse, TorrentRejected, TorrentNotFound,
            DeleteRefused, ValueError} <= kinds
    for e in errors:
        assert PASSWORD not in str(e) and PASSWORD not in repr(e) and PASSWORD not in repr(e.args)
        assert e.__cause__ is None or PASSWORD not in repr(e.__cause__)
    out = capsys.readouterr()
    assert PASSWORD not in out.out and PASSWORD not in out.err


def test_a_transport_failure_does_not_chain_the_original_exception(qbt):
    c = QbtClient(QbtConnection("http://127.0.0.1:9", USERNAME, PASSWORD), timeout=1)
    with pytest.raises(Unreachable) as err:
        c.version()
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_an_injected_session_is_used_and_marked_as_no_proxy():
    s = requests.Session()
    c = QbtClient(QbtConnection("http://nas:8080", USERNAME, PASSWORD), session=s)
    assert c._session is s and s.trust_env is False
    assert s.headers["User-Agent"].startswith("MangaList/")
