"""A fake qBittorrent Web UI (an ``http.server`` in a thread, loopback only): login with the SID cookie and
qBittorrent's ``Ok.`` / ``Fails.`` / 403-ban answers, the Origin / Referer check, categories, torrents, files, add
and delete. It records every request so tests can see what was (and was not) sent."""

from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

import pytest

from mangalist.downloads.contracts import QbtConnection

USERNAME = "admin"
PASSWORD = "Sup3r-Secret-Pa55word-DoNotLog"
HASH_A = "a" * 40
HASH_B = "b" * 40
HASH_C = "c" * 40


class FakeQbt:
    def __init__(self) -> None:
        self.username = USERNAME
        self.password = PASSWORD
        self.version = "v5.2.4"
        self.ban_after = 5
        self.failed_logins = 0
        self.banned = False
        self.sessions: set = set()
        self.categories: Dict[str, Dict[str, str]] = {}
        self.torrents: Dict[str, Dict[str, Any]] = {}
        self.files: Dict[str, List[Dict[str, Any]]] = {}
        self.requests: List[Dict[str, Any]] = []
        self.deleted: List[Dict[str, Any]] = []
        self.added: List[tuple] = []                 # (link, category)
        self.add_answer = "Ok."
        self.login_status_override: Optional[int] = None
        self.login_body_override: Optional[str] = None
        self.check_origin = True
        self.fail_with: Dict[str, int] = {}          # path -> status to answer
        self.lock = threading.Lock()

    # --- helpers for tests ---------------------------------------------------------------------------

    def put_torrent(self, info_hash: str, *, name: str = "Some Release", category: str = "mangalist",
                    state: str = "stoppedUP", progress: float = 1.0, ratio: float = 2.0, seeding_time: int = 5000,
                    save_path: str = "/data/appdata/torrents/mangalist") -> None:
        self.torrents[info_hash] = {
            "hash": info_hash, "name": name, "category": category, "state": state, "progress": progress,
            "ratio": ratio, "seeding_time": seeding_time, "save_path": save_path,
            "content_path": f"{save_path}/{name}", "size": 123456, "num_seeds": 0}

    def expire_sessions(self) -> None:
        self.sessions.clear()

    def calls(self, path: str) -> List[Dict[str, Any]]:
        return [r for r in self.requests if r["path"] == path]

    # --- the API -------------------------------------------------------------------------------------

    def handle(self, method: str, path: str, query: Dict[str, str], form: Dict[str, str], headers: Dict[str, str]):
        """-> (status, body bytes, extra headers)"""
        with self.lock:
            self.requests.append({"method": method, "path": path, "query": dict(query), "form": dict(form),
                                  "headers": dict(headers)})
            if self.check_origin:
                host = headers.get("host", "")
                for key in ("origin", "referer"):
                    value = headers.get(key)
                    if value is not None and urlsplit(value).netloc != host:
                        return 401, b"Unauthorized", {}
            if path in self.fail_with:
                return self.fail_with[path], b"boom", {}
            if path == "auth/login":
                return self._login(form)
            if headers.get("cookie_sid") not in self.sessions:
                return 403, b"Forbidden", {}
            return self._api(method, path, query, form)

    def _login(self, form: Dict[str, str]):
        if self.login_status_override is not None:
            return self.login_status_override, (self.login_body_override or "").encode(), {}
        if self.banned:
            return 403, b"Your IP address has been banned after too many failed authentication attempts.", {}
        if form.get("username") == self.username and form.get("password") == self.password:
            sid = secrets.token_hex(16)
            self.sessions.add(sid)
            return 200, (self.login_body_override or "Ok.").encode(), {"Set-Cookie": f"SID={sid}; HttpOnly; path=/"}
        self.failed_logins += 1
        if self.failed_logins >= self.ban_after:
            self.banned = True
        return 200, b"Fails.", {}

    def _json(self, value: Any):
        return 200, json.dumps(value).encode(), {"Content-Type": "application/json"}

    def _api(self, method: str, path: str, query: Dict[str, str], form: Dict[str, str]):
        if path == "app/version":
            return 200, self.version.encode(), {}
        if path == "torrents/categories":
            return self._json(self.categories)
        if path == "torrents/createCategory":
            name = form.get("category", "")
            if not name:
                return 400, b"", {}
            self.categories[name] = {"name": name, "savePath": form.get("savePath", "")}
            return 200, b"", {}
        if path == "torrents/editCategory":
            name = form.get("category", "")
            if name not in self.categories:
                return 409, b"", {}
            self.categories[name]["savePath"] = form.get("savePath", "")
            return 200, b"", {}
        if path == "torrents/info":
            rows = list(self.torrents.values())
            if "category" in query:
                wanted = query["category"]
                # real qBittorrent (sub-categories on) also lists "wanted/child"
                rows = [t for t in rows if t["category"] == wanted or t["category"].startswith(wanted + "/")]
            if "hashes" in query:
                keep = set(query["hashes"].split("|"))
                rows = [t for t in rows if t["hash"] in keep]
            return self._json(rows)
        if path == "torrents/files":
            h = query.get("hash", "")
            if h not in self.torrents:
                return 404, b"", {}
            return self._json(self.files.get(h, []))
        if path == "torrents/add":
            if self.add_answer == "Ok.":
                for link in form.get("urls", "").split("\n"):
                    self.added.append((link, form.get("category")))
            return 200, self.add_answer.encode(), {}
        if path == "torrents/delete":
            hashes = form.get("hashes", "")
            targets = list(self.torrents) if hashes == "all" else hashes.split("|")
            for h in targets:
                if h in self.torrents:
                    self.deleted.append({"hash": h, "category": self.torrents[h]["category"],
                                         "deleteFiles": form.get("deleteFiles")})
                    del self.torrents[h]
            return 200, b"", {}
        return 404, b"", {}


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    fake: FakeQbt

    def log_message(self, *args: Any) -> None:  # silence
        pass

    def _serve(self, method: str) -> None:
        parts = urlsplit(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        form = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
        query = {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()}
        headers = {k.lower(): v for k, v in self.headers.items()}
        cookie = headers.get("cookie", "")
        headers["cookie_sid"] = next((c.split("=", 1)[1] for c in (x.strip() for x in cookie.split(";"))
                                      if c.startswith("SID=")), "")
        prefix = "/api/v2/"
        if not parts.path.startswith(prefix):
            status, body, extra = 404, b"", {}
        else:
            status, body, extra = self.fake.handle(method, parts.path[len(prefix):], query, form, headers)
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")


@pytest.fixture
def qbt():
    fake = FakeQbt()
    handler = type("Handler", (_Handler,), {"fake": fake})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def conn(qbt):
    return QbtConnection(base_url=qbt.url, username=USERNAME, password=PASSWORD)


@pytest.fixture
def client(conn):
    from mangalist.services.qbittorrent import QbtClient

    c = QbtClient(conn)
    yield c
    c.close()
