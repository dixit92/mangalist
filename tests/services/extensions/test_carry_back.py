"""The carry-back from the built-in extension host to Suwayomi (transition audit B8, 2026-10-24): what was decided on the
host - a title match confirmed, Change source, a group picked - reaches Suwayomi's choices, found there by the manga's
url on the same source; what was carried over from Suwayomi is left alone; a second run changes nothing. The fake host
over real HTTP, a fake Suwayomi in memory. Made-up names."""

from __future__ import annotations

import json

import pytest

from mangalist import store
from mangalist.services.extensions import carry_over as co
from mangalist.services.extensions.client import ExtensionHostClient
from mangalist.store.downloads import META_EXTENSIONS_SERIES, META_SUWAYOMI_SERIES, SuwayomiSettings

from .carry_fakes import LOST_ID, MANGADEX, WEEB, FakeSuwayomiCarry, raw_choice, set_suwayomi_choice
from .fake_host import MANGADEX_EN, MANGADEX_PKG, WEEB_ID, WEEB_PKG, FakeHost

MD_URL = "/manga/00000000-0000-4000-8000-0000000c0000"


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def host(tmp_path):
    h = FakeHost(tmp_path / "host", auto_download=False).start()
    h.installed.update({MANGADEX_PKG: "1.4.200", WEEB_PKG: "1.4.3"})
    yield h
    h.stop()


@pytest.fixture
def suwayomi():
    s = FakeSuwayomiCarry()
    s.add(11, MANGADEX, MD_URL, "Example Manga")
    s.add(12, WEEB, "/series/weeb-old", "Weeb Story")
    return s


def host_choice(db, sid, **data):
    db.set_meta(f"{META_EXTENSIONS_SERIES}{sid}", json.dumps(data))


def run(db, suwayomi, host, **kw):
    client = ExtensionHostClient(host.url, timeout=10)
    try:
        return co.carry_back(db, suwayomi, client, pause=0, **kw)
    finally:
        client.close()


def test_what_was_decided_on_the_host_reaches_suwayomi(db, suwayomi, host):
    client = ExtensionHostClient(host.url)
    weeb_new = client.register_manga(WEEB_ID, "/series/weeb-new", "Weeb Story (new)")["id"]
    md = client.register_manga(MANGADEX_EN, MD_URL, "Example Manga")["id"]
    # 1: carried over from Suwayomi, unchanged: left alone. 2: Change source on the host (no url stored: asked of the
    # host). 3: a series added on the host, Suwayomi has none, found by a search. 4: only a group picked on the host.
    set_suwayomi_choice(db, 1, 11, MANGADEX_EN, "Example Manga")
    host_choice(db, 1, source_id=MANGADEX_EN, manga_id=md, how="mangadex-id", carried_from={"suwayomi_manga_id": 11})
    set_suwayomi_choice(db, 2, 12, WEEB_ID, "Weeb Story", "confirmed", group="Old Team")
    host_choice(db, 2, source_id=WEEB_ID, manga_id=weeb_new, manga_title="Weeb Story (new)", how="confirmed",
                group="New Team")
    suwayomi.searchable[(WEEB_ID, "/series/weeb-new")] = "Weeb Story (new)"
    host_choice(db, 3, source_id=MANGADEX_EN, manga_id=md, manga_url=MD_URL, manga_title="Example Manga",
                how="mangadex-id")
    set_suwayomi_choice(db, 4, 11, MANGADEX_EN, "Example Manga", group="Alpha")
    host_choice(db, 4, group_cleared=True)
    host_choice(db, 5, source_id=LOST_ID, manga_id=99, manga_url="/lost/1", how="confirmed")

    plan = run(db, suwayomi, host, dry_run=True)
    assert {r.series_id: r.status for r in plan.series} == {1: co.BACK_SAME, 2: co.BACK_CARRIED, 3: co.BACK_CARRIED,
                                                            4: co.BACK_GROUP, 5: co.BACK_NO_SOURCE}
    assert raw_choice(db, 2, META_SUWAYOMI_SERIES)["manga_id"] == 12            # a dry run writes nothing

    report = run(db, suwayomi, host)
    assert report.to_do() == 3
    two = raw_choice(db, 2, META_SUWAYOMI_SERIES)
    assert two["manga_id"] == 101 and two["manga_url"] == "/series/weeb-new" and two["group"] == "New Team"
    assert two["carried_back"]["host_manga_id"] == weeb_new
    three = raw_choice(db, 3, META_SUWAYOMI_SERIES)
    assert (three["source_id"], three["manga_id"], three["how"]) == (MANGADEX_EN, 11, "mangadex-id")
    assert "group" not in raw_choice(db, 4, META_SUWAYOMI_SERIES)
    assert raw_choice(db, 1, META_SUWAYOMI_SERIES)["manga_id"] == 11
    # Suwayomi's choices are what Suwayomi chosen reads
    assert SuwayomiSettings(db).series_choice(2)["manga_id"] == 101

    again = run(db, suwayomi, host)
    assert again.to_do() == 0
    assert co.not_yet_carried_back(db) == 1                                      # the lost source's one


def test_a_series_suwayomi_cannot_find_is_reported(db, suwayomi, host):
    md = ExtensionHostClient(host.url).register_manga(MANGADEX_EN, "/manga/unknown", "Unknown")["id"]
    host_choice(db, 7, source_id=MANGADEX_EN, manga_id=md, how="confirmed")
    report = run(db, suwayomi, host)
    (row,) = report.series
    assert row.status == co.BACK_UNKNOWN and "chapters panel" in row.message
    assert raw_choice(db, 7, META_SUWAYOMI_SERIES) is None
    assert "Suwayomi does not find the manga 1" in "\n".join(report.lines())


def test_suwayomi_down_stops_it_before_anything_is_written(db, suwayomi, host):
    host_choice(db, 3, source_id=MANGADEX_EN, manga_id=1, manga_url=MD_URL, how="confirmed")
    suwayomi.unreachable = True
    with pytest.raises(co.CarryOverError, match="Suwayomi could not be asked"):
        run(db, suwayomi, host)


def test_the_command_line_carries_back(db, suwayomi, host, monkeypatch, capsys):
    from mangalist import paths
    from mangalist.services.extensions.__main__ import main

    host_choice(db, 3, source_id=MANGADEX_EN, manga_id=1, manga_url=MD_URL, manga_title="Example Manga",
                how="mangadex-id")
    monkeypatch.setattr(co, "_host_up", lambda _db, _url: None)
    monkeypatch.setattr(co, "clients_from_store",
                        lambda _db, host_url=None, timeout=120.0: (suwayomi, ExtensionHostClient(host.url)))
    assert main(["carry-back", "--dry-run", "--db", str(paths.db_file())]) == 0
    assert capsys.readouterr().out.startswith("Would carry back to Suwayomi: Would carry back 1")
    assert main(["carry-back", "--db", str(paths.db_file())]) == 0
    assert raw_choice(db, 3, META_SUWAYOMI_SERIES)["manga_id"] == 11
