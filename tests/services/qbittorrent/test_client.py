from __future__ import annotations

import pytest

from mangalist.downloads.contracts import QBITTORRENT_CATEGORY, QbtConnection, TorrentClient
from mangalist.services.qbittorrent import (
    AuthFailed, DeleteRefused, IpBanned, QbtClient, TorrentNotFound, TorrentRejected, UnexpectedResponse,
    Unreachable, normalize_base_url,
)

from .conftest import HASH_A, HASH_B, HASH_C, PASSWORD, USERNAME


def test_implements_the_contract(client):
    c: TorrentClient = client
    assert c.version() == "v5.2.4"


# --- login --------------------------------------------------------------------------------------------

def test_login_sends_referer_and_origin_and_keeps_the_sid_cookie(client, qbt):
    assert client.version() == "v5.2.4"
    login = qbt.calls("auth/login")[0]
    assert login["method"] == "POST"
    assert login["form"] == {"username": USERNAME, "password": PASSWORD}
    assert login["headers"]["referer"] == qbt.url + "/"
    assert login["headers"]["origin"] == qbt.url
    assert len(qbt.sessions) == 1
    client.version()
    assert len(qbt.calls("auth/login")) == 1          # one login serves every later call
    assert qbt.calls("app/version")[1]["headers"]["cookie_sid"] in qbt.sessions


def test_login_is_lazy(conn, qbt):
    QbtClient(conn)
    assert qbt.requests == []


def test_wrong_password_is_auth_failed(qbt):
    c = QbtClient(QbtConnection(qbt.url, USERNAME, "wrong"))
    with pytest.raises(AuthFailed) as err:
        c.version()
    assert not isinstance(err.value, IpBanned)
    assert "wrong" not in str(err.value)


def test_wrong_credentials_are_never_retried(qbt):
    c = QbtClient(QbtConnection(qbt.url, USERNAME, "wrong"))
    for _ in range(10):
        with pytest.raises(AuthFailed):
            c.version()
    assert qbt.failed_logins == 1 and len(qbt.calls("auth/login")) == 1     # the ban counter stays at one
    assert not qbt.banned


def test_a_banned_address_is_ip_banned(qbt):
    qbt.banned = True
    c = QbtClient(QbtConnection(qbt.url, USERNAME, PASSWORD))
    with pytest.raises(IpBanned):
        c.version()
    with pytest.raises(IpBanned):
        c.version()
    assert len(qbt.calls("auth/login")) == 1


def test_a_proxy_401_on_login_is_auth_failed(qbt, client):
    qbt.login_status_override = 401
    with pytest.raises(AuthFailed):
        client.version()


def test_an_unknown_login_answer_is_unexpected(qbt, client):
    qbt.login_status_override, qbt.login_body_override = 200, "<html>router</html>"
    with pytest.raises(UnexpectedResponse):
        client.version()


def test_a_server_error_on_login_is_unexpected(qbt, client):
    qbt.login_status_override = 500
    with pytest.raises(UnexpectedResponse) as err:
        client.version()
    assert err.value.status == 500


def test_an_expired_session_logs_in_again_once(client, qbt):
    client.version()
    qbt.expire_sessions()
    assert client.version() == "v5.2.4"
    assert len(qbt.calls("auth/login")) == 2
    assert [r["path"] for r in qbt.requests].count("app/version") == 3     # ok, refused, repeated


def test_a_session_that_stays_refused_is_auth_failed_after_one_relogin(client, qbt, monkeypatch):
    client.version()
    # the server keeps refusing the API with 403 even though the login says Ok.
    original = qbt._api
    monkeypatch.setattr(qbt, "_api", lambda *a, **k: (403, b"Forbidden", {}))
    with pytest.raises(AuthFailed):
        client.version()
    assert len(qbt.calls("auth/login")) == 2
    monkeypatch.setattr(qbt, "_api", original)


def test_the_origin_check_is_satisfied_even_with_a_proxy_sub_path(qbt):
    assert normalize_base_url(qbt.url + "/qbt/api/v2/") == qbt.url + "/qbt"


def test_unreachable_server():
    c = QbtClient(QbtConnection("http://127.0.0.1:9", USERNAME, PASSWORD), timeout=2)
    with pytest.raises(Unreachable):
        c.version()


def test_a_non_qbittorrent_answer_is_unexpected(qbt, client):
    qbt.version = "<html>hello</html>"
    with pytest.raises(UnexpectedResponse):
        client.version()


def test_http_errors_are_unexpected_responses(qbt, client):
    qbt.fail_with["app/version"] = 502
    with pytest.raises(UnexpectedResponse) as err:
        client.version()
    assert err.value.status == 502


# --- addresses ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("192.168.1.10:8080", "http://192.168.1.10:8080"),
    ("http://nas:8080/", "http://nas:8080"),
    ("HTTPS://Nas.local:8443/api/v2", "https://Nas.local:8443"),
    ("http://nas/qbittorrent/", "http://nas/qbittorrent"),
])
def test_normalize_base_url(given, expected):
    assert normalize_base_url(given) == expected


@pytest.mark.parametrize("given", ["", "   ", "ftp://nas:8080", "http://user:pw@nas:8080", "http://nas?x=1",
                                   "http://nas#frag", "http://"])
def test_bad_addresses_are_refused(given):
    with pytest.raises(ValueError) as err:
        normalize_base_url(given)
    assert "pw" not in str(err.value)


# --- categories ---------------------------------------------------------------------------------------

def test_ensure_category_creates_it(client, qbt):
    client.ensure_category(QBITTORRENT_CATEGORY, "/data/appdata/torrents/mangalist")
    assert qbt.categories["mangalist"]["savePath"] == "/data/appdata/torrents/mangalist"
    assert qbt.calls("torrents/createCategory")[0]["form"] == {
        "category": "mangalist", "savePath": "/data/appdata/torrents/mangalist"}


def test_ensure_category_leaves_a_matching_category_alone(client, qbt):
    qbt.categories["mangalist"] = {"name": "mangalist", "savePath": "/data/x/"}
    client.ensure_category("mangalist", "/data/x")
    assert not qbt.calls("torrents/createCategory") and not qbt.calls("torrents/editCategory")


def test_ensure_category_corrects_a_different_save_path(client, qbt):
    qbt.categories["mangalist"] = {"name": "mangalist", "savePath": "/old"}
    client.ensure_category("mangalist", "/data/appdata/torrents/mangalist")
    assert qbt.categories["mangalist"]["savePath"] == "/data/appdata/torrents/mangalist"
    assert len(qbt.calls("torrents/editCategory")) == 1


def test_ensure_category_needs_a_name(client):
    with pytest.raises(ValueError):
        client.ensure_category("  ", "/x")


def test_ensure_category_reports_a_refusal(client, qbt):
    qbt.fail_with["torrents/createCategory"] = 409
    with pytest.raises(UnexpectedResponse):
        client.ensure_category("bad//name", "/x")


# --- add ----------------------------------------------------------------------------------------------

def test_add_a_torrent_url(client, qbt):
    client.add("https://nyaa.si/download/123.torrent", category="mangalist")
    assert qbt.added == [("https://nyaa.si/download/123.torrent", "mangalist")]
    form = qbt.calls("torrents/add")[0]["form"]
    assert form == {"urls": "https://nyaa.si/download/123.torrent", "category": "mangalist", "autoTMM": "true"}


def test_add_a_magnet_link(client, qbt):
    client.add(f"magnet:?xt=urn:btih:{HASH_A}", category="mangalist")
    assert qbt.added[0][0] == f"magnet:?xt=urn:btih:{HASH_A}"


def test_add_refused_is_torrent_rejected(client, qbt):
    qbt.add_answer = "Fails."
    with pytest.raises(TorrentRejected):
        client.add(f"magnet:?xt=urn:btih:{HASH_A}", category="mangalist")


@pytest.mark.parametrize("url", ["", "  ", "file:///etc/passwd", "ftp://x/y.torrent", "no scheme",
                                 "https://a/1.torrent\nhttps://b/2.torrent", "https://a/1.torrent https://b/2",
                                 "magnet:?xt=urn:btih:aa\rmagnet:?xt=urn:btih:bb"])
def test_add_takes_exactly_one_safe_link(client, qbt, url):
    with pytest.raises(ValueError):
        client.add(url, category="mangalist")
    assert not qbt.calls("torrents/add")


def test_add_needs_a_category(client):
    with pytest.raises(ValueError):
        client.add("magnet:?xt=urn:btih:" + HASH_A, category="")


# --- listing ------------------------------------------------------------------------------------------

def test_torrents_maps_the_fields(client, qbt):
    qbt.put_torrent(HASH_A.upper().lower(), name="Series v01", state="stoppedUP", progress=1.0, ratio=2.04,
                    seeding_time=7200, save_path="/data/appdata/torrents/mangalist")
    (t,) = client.torrents("mangalist")
    assert (t.info_hash, t.name, t.category, t.state) == (HASH_A, "Series v01", "mangalist", "stoppedUP")
    assert (t.progress, t.ratio, t.seeding_time) == (1.0, 2.04, 7200)
    assert t.save_path == "/data/appdata/torrents/mangalist"
    assert t.content_path == "/data/appdata/torrents/mangalist/Series v01"
    assert t.complete and t.stopped_complete


def test_torrents_lists_only_the_asked_category_exactly(client, qbt):
    qbt.put_torrent(HASH_A, category="mangalist")
    qbt.put_torrent(HASH_B, category="movies")
    qbt.put_torrent(HASH_C, category="mangalist/child")          # the server includes sub-categories
    qbt.put_torrent("d" * 40, category="")
    assert [t.info_hash for t in client.torrents("mangalist")] == [HASH_A]
    assert qbt.calls("torrents/info")[0]["query"] == {"category": "mangalist"}


def test_torrents_refuses_an_empty_category(client, qbt):
    qbt.put_torrent(HASH_B, category="")
    with pytest.raises(ValueError):
        client.torrents("")
    assert not qbt.calls("torrents/info")


def test_v4_style_states_work_too(client, qbt):
    qbt.put_torrent(HASH_A, state="pausedUP")
    qbt.put_torrent(HASH_B, state="downloading", progress=0.4)
    states = {t.info_hash: (t.complete, t.stopped_complete) for t in client.torrents("mangalist")}
    assert states == {HASH_A: (True, True), HASH_B: (False, False)}


def test_files(client, qbt):
    qbt.put_torrent(HASH_A)
    qbt.files[HASH_A] = [{"name": "Series v01/Series v01.cbz", "size": 100, "progress": 1.0},
                         {"name": "Series v01\\cover.jpg", "size": 5, "progress": 0.5}]
    files = client.files(HASH_A.upper())
    assert [(f.name, f.size, f.progress) for f in files] == [
        ("Series v01/Series v01.cbz", 100, 1.0), ("Series v01/cover.jpg", 5, 0.5)]
    assert qbt.calls("torrents/files")[0]["query"] == {"hash": HASH_A}


def test_files_of_an_unknown_torrent(client):
    with pytest.raises(TorrentNotFound):
        client.files(HASH_A)


@pytest.mark.parametrize("bad", ["", "abc", "a" * 39, "g" * 40, "all", HASH_A + "|" + HASH_B])
def test_hashes_are_validated(client, qbt, bad):
    with pytest.raises(ValueError):
        client.files(bad)
    with pytest.raises(ValueError):
        client.delete(bad, delete_files=True)
    assert not qbt.calls("torrents/files") and not qbt.calls("torrents/delete")


# --- delete: defence in depth -------------------------------------------------------------------------

def test_delete_in_the_mangalist_category(client, qbt):
    qbt.put_torrent(HASH_A, category=QBITTORRENT_CATEGORY)
    client.delete(HASH_A, delete_files=True)
    assert qbt.deleted == [{"hash": HASH_A, "category": "mangalist", "deleteFiles": "true"}]
    assert qbt.calls("torrents/delete")[0]["form"] == {"hashes": HASH_A, "deleteFiles": "true"}


def test_delete_without_files(client, qbt):
    qbt.put_torrent(HASH_A)
    client.delete(HASH_A, delete_files=False)
    assert qbt.deleted[0]["deleteFiles"] == "false"


@pytest.mark.parametrize("category", ["", "movies", "radarr", "Mangalist", "mangalist/child", "mangalist ", "manga"])
def test_delete_refuses_any_other_category(client, qbt, category):
    qbt.put_torrent(HASH_A, category=category)
    with pytest.raises(DeleteRefused):
        client.delete(HASH_A, delete_files=True)
    assert qbt.deleted == [] and not qbt.calls("torrents/delete") and HASH_A in qbt.torrents


def test_delete_looks_at_the_torrent_first(client, qbt):
    qbt.put_torrent(HASH_A)
    client.delete(HASH_A, delete_files=True)
    paths = [r["path"] for r in qbt.requests if r["path"].startswith("torrents/")]
    assert paths == ["torrents/info", "torrents/delete"]
    assert qbt.calls("torrents/info")[0]["query"] == {"hashes": HASH_A}


def test_delete_of_an_unknown_torrent_is_not_found_and_sends_no_delete(client, qbt):
    with pytest.raises(TorrentNotFound):
        client.delete(HASH_A, delete_files=True)
    assert not qbt.calls("torrents/delete")


def test_delete_never_touches_a_neighbour(client, qbt):
    qbt.put_torrent(HASH_A)
    qbt.put_torrent(HASH_B, category="movies")
    qbt.put_torrent(HASH_C)
    client.delete(HASH_A, delete_files=True)
    assert sorted(qbt.torrents) == [HASH_B, HASH_C]
    assert all(r["form"].get("hashes") != "all" for r in qbt.calls("torrents/delete"))


def test_delete_refuses_when_the_server_returns_a_different_torrent(client, qbt, monkeypatch):
    qbt.put_torrent(HASH_B)
    real = qbt._api

    def wrong(method, path, query, form):
        if path == "torrents/info":
            return qbt._json([qbt.torrents[HASH_B]])      # asked for A, got B
        return real(method, path, query, form)

    monkeypatch.setattr(qbt, "_api", wrong)
    with pytest.raises(TorrentNotFound):
        client.delete(HASH_A, delete_files=True)
    assert qbt.deleted == []


# --- qBittorrent 5.x answers (the owner's 5.2.4: an empty 204 on a good login) --------------------------------


def test_a_v5_login_is_an_empty_204_with_a_port_named_cookie(qbt, client):
    qbt.v5 = True
    assert client.version() == qbt.version
    assert len(qbt.calls("auth/login")) == 1


def test_a_v5_refused_login_is_auth_failed_and_never_retried(qbt, conn):
    from mangalist.downloads.contracts import QbtConnection
    from mangalist.services.qbittorrent import QbtClient
    qbt.v5 = True
    c = QbtClient(QbtConnection(conn.base_url, conn.username, "wrong", conn.verify_tls))
    for _ in range(2):
        with pytest.raises(AuthFailed):
            c.version()
    assert len(qbt.calls("auth/login")) == 1


def test_a_204_login_without_a_session_cookie_is_unexpected(qbt, client):
    qbt.login_status_override = 204
    with pytest.raises(UnexpectedResponse, match="no session cookie"):
        client.version()
