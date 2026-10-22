"""The built-in extensions backend end to end against the REAL extension host, with a REAL download (opt-in; skipped by
default - it needs the host, a JVM and the network).

    MANGALIST_E2E_EXTENSIONS=<the host's install dir (host/build/install/mangalist-extension-host)>   # started here
    MANGALIST_E2E_EXTENSIONS=http://127.0.0.1:4570                                                   # or a running host
    MANGALIST_E2E_DOWNLOADS_DIR=<the running host's downloads folder as this test sees it>   (URL mode, when different)
    MANGALIST_E2E_WEBVIEW=1                       # also the WebView case (Comix: the host's KCEF Chromium)
    MANGALIST_E2E_FLARESOLVERR=http://192.168.2.50:8191   # also the Cloudflare case (ManhwaRead through FlareSolverr)

    python -m pytest -q tests/e2e/test_extensions_live.py -s

Each case: a temporary data folder and library root with one series folder; the extension installed through the
Extensions page's call; the series found, sent (MangaList's real send path, the built-in backend chosen), downloaded by
the host and filed by the chapter arrivals pass; the CBZ asserted in the series folder under MangaList's scheme name, with
its pages and ComicInfo, and the host's copy deleted (:mod:`.harness`). The chapter is the shortest of the newest few the
source lists for the series (fewest pages), to keep the download small.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Iterator, List, Optional

import pytest

from mangalist.downloads.adapter import Backend
from mangalist.services.extensions import ExtensionHostClient, ExtensionHostError
from mangalist.services.extensions.compat import ExtensionsChapterClient

from .harness import assert_filed, choose_builtin, download_one_chapter, library_with_series

ENV = "MANGALIST_E2E_EXTENSIONS"
TARGET = os.environ.get(ENV, "").strip()
FLARESOLVERR = os.environ.get("MANGALIST_E2E_FLARESOLVERR", "").strip()
WEBVIEW = os.environ.get("MANGALIST_E2E_WEBVIEW", "").strip().lower() in ("1", "true", "yes", "on")

pytestmark = pytest.mark.skipif(not TARGET, reason=f"opt-in: set {ENV} to the host's install dir or a running host's URL")

#: (extension name in the repository, source language, a series of the owner's library on it) - the demo's rows
#: (mangalist/services/extensions/__main__.py), the sources MangaList's series use most.
MANGADEX = ("MangaDex", "en", "Kaoru Hana wa Rin to Saku")
COMIX = ("Comix", "en", "Shuden Sugitara Nani Shite Asobu")
MANHWAREAD = ("ManhwaRead", "en", "Moby Dick")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(url: str, process: Optional[subprocess.Popen], timeout: float = 180.0) -> None:
    client = ExtensionHostClient(url, timeout=5.0)
    end = time.monotonic() + timeout
    try:
        while time.monotonic() < end:
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"the host exited with status {process.returncode}")
            try:
                client.health()
                return
            except ExtensionHostError:
                time.sleep(1.0)
        raise RuntimeError(f"the host did not answer at {url} within {timeout:.0f} s")
    finally:
        client.close()


@pytest.fixture
def live_host(tmp_path) -> Iterator[dict]:
    """``{url, download_dir}``: the host started here (from its install dir, with its data in a temporary folder) or
    the running one ``MANGALIST_E2E_EXTENSIONS`` names."""
    if TARGET.startswith(("http://", "https://")):
        yield {"url": TARGET.rstrip("/"), "download_dir": os.environ.get("MANGALIST_E2E_DOWNLOADS_DIR", "")}
        return
    launcher = Path(TARGET) / "bin" / "mangalist-extension-host"
    if not launcher.is_file():
        pytest.fail(f"{ENV}: no {launcher}")
    if shutil.which("java") is None and not os.environ.get("JAVA_HOME"):
        pytest.fail("the host needs Java 21 (java on PATH or JAVA_HOME)")
    data, port = tmp_path / "exthost", _free_port()
    args = [str(launcher), "--data", str(data), "--port", str(port), "--downloads", str(data / "downloads"),
            "--webview", "on" if WEBVIEW else "off"]
    if FLARESOLVERR:
        args += ["--flaresolverr", FLARESOLVERR]
    log = open(tmp_path / "exthost.log", "wb")
    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_up(url, process)
        yield {"url": url, "download_dir": ""}
    finally:
        process.terminate()
        try:
            process.wait(30)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()
        print(f"\n--- host log ({tmp_path / 'exthost.log'}):\n" +
              (tmp_path / "exthost.log").read_text(errors="replace")[-4000:])


def _install(backend: Backend, name: str, lang: str) -> str:
    """Install the extension through the Extensions page's call; the id of its source in *lang*."""
    rows = [r for r in backend.extension_repo() if r.name.casefold() == name.casefold()]
    assert rows, f"{name} is not in the repository"
    if not rows[0].installed:
        backend.install_extension(rows[0].pkg)
    host = ExtensionHostClient(backend.load_extension_host().url)
    try:
        sources = [s for s in host.sources() if s.name.casefold() == name.casefold() and s.lang == lang]
    finally:
        host.close()
    assert sources, f"{name} installed but no {lang} source loaded"
    return sources[0].id


def _shortest_chapter(url: str, source_id: str, query: str, newest: int = 6) -> tuple:
    """(manga title, chapter number) of the chapter with the fewest pages among the newest few the source lists."""
    compat = ExtensionsChapterClient(base_url=url, timeout=300.0)
    host = ExtensionHostClient(url)
    try:
        found = [m for m in compat.search(source_id, query) if query.casefold() in m.title.casefold()] or \
            compat.search(source_id, query)
        assert found, f"{query!r}: nothing found"
        manga = found[0]
        chapters = [c for c in compat.chapters(manga.id).chapters if c.number != "-1"][:newest]
        assert chapters, f"{manga.title!r}: no numbered chapter"
        sizes: List[tuple] = []
        for c in chapters:
            try:
                sizes.append((len(host.pages(source_id, c.url)), c))
            except ExtensionHostError:
                continue
        assert sizes, "no chapter's pages could be listed"
        sizes.sort(key=lambda t: t[0])
        print(f"\n{manga.title!r}: chapter {sizes[0][1].number} ({sizes[0][0]} pages, {sizes[0][1].scanlator or 'no group'})")
        return manga.title, sizes[0][1].number, sizes[0][1].scanlator
    finally:
        compat.close()
        host.close()


def _case(db, tmp_path, live_host, row, flaresolverr: str = "") -> None:
    name, lang, query = row
    backend = Backend(db)
    choose_builtin(backend, live_host["url"], live_host["download_dir"], flaresolverr)
    source_id = _install(backend, name, lang)
    title, number, group = _shortest_chapter(live_host["url"], source_id, query)
    sid = library_with_series(db, tmp_path, query)
    out = download_one_chapter(db, backend, sid, source_id=source_id, titles=(query, title), number=number,
                               pick_candidate=lambda m: m.manga.title == title, group=group or "", timeout=900.0,
                               pause=2.0)
    assert_filed(out, number)
    print(f"{name}: filed {out.filed.name} ({len(out.pages)} pages) in {out.seconds:.0f} s")


def test_mangadex_one_english_chapter(db, tmp_path, live_host):
    _case(db, tmp_path, live_host, MANGADEX)


@pytest.mark.skipif(not WEBVIEW, reason="opt-in: MANGALIST_E2E_WEBVIEW=1 (the host's WebView: KCEF Chromium)")
def test_a_webview_source(db, tmp_path, live_host):
    _case(db, tmp_path, live_host, COMIX)


@pytest.mark.skipif(not FLARESOLVERR, reason="opt-in: MANGALIST_E2E_FLARESOLVERR=<FlareSolverr's URL>")
def test_a_source_behind_cloudflare(db, tmp_path, live_host):
    _case(db, tmp_path, live_host, MANHWAREAD, FLARESOLVERR)
