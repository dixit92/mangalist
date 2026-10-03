"""Sync into the database: full / incremental, removals, carriedFrom, 409, 401 stop, kinds, archives."""

from __future__ import annotations

import json
import logging

from mangalist.services.mangapixer import client as mpc
from mangalist.services.mangapixer.sync import sync_all, sync_library

from .conftest import TOKEN, folder, load_fixture


def _sync(cache, client_factory, fake, **kw):
    return sync_all(cache, client=client_factory(fake.url), list_series=lambda rid: [], **kw)


def test_full_sync_from_the_contract_sample(connected, fake, client_factory):
    sample = load_fixture()
    fake.items["lib0manga"] = sample["items"]
    fake.server_time = sample["serverTime"]
    res = _sync(connected, client_factory, fake)
    assert res.status == "ok", res.message
    lib = res.libraries[0]
    assert (lib.mode, lib.upserted, lib.archives_dropped) == ("full", 3, 1)
    assert "updatedSince" not in fake.requests[-1]["query"]
    rows = {r.node_id: r for r in connected.items("lib0manga")}
    assert set(rows) == {"n0002", "n0011", "n0013"}            # the archive n0010 is dropped
    assert rows["n0011"].link_state == "DontMatch"
    # The item JSON is kept exactly as given.
    assert rows["n0002"].item == sample["items"][0]
    assert connected.sync_state("lib0manga").server_time == sample["serverTime"]
    assert connected.sync_state("lib0manga").last_mode == "full"
    assert connected.last_sync_at()


def test_incremental_uses_the_first_pages_server_time_and_applies_removals_first(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["A"]), folder("n0002", ["B"]), folder("n0003", ["C"])]
    fake.server_time = "2026-10-01T00:00:00.000Z"
    _sync(connected, client_factory, fake)
    # Later: B is gone, C changed, D is new; A's carry-over moves it to A2 (re-key, not remove + add).
    fake.server_time = "2026-10-02T00:00:00.000Z"
    fake.items["lib0manga"] = [
        folder("n0003", ["C"], state="NeedsReview", updated="2026-10-01T10:00:00.000Z"),
        folder("n0004", ["D"], updated="2026-10-01T11:00:00.000Z"),
        folder("n0005", ["A2"], carried_from="n0001", updated="2026-10-01T12:00:00.000Z"),
    ]
    fake.removed["lib0manga"] = [{"nodeId": "n0002", "reason": "nodeGone", "at": "2026-10-01T09:00:00.000Z"},
                                 {"nodeId": "n0001x", "reason": "linkCleared", "at": "2026-10-01T09:00:00.000Z"}]
    res = _sync(connected, client_factory, fake, limit=2)
    lib = res.libraries[0]
    assert lib.mode == "incremental" and lib.pages == 2
    assert [r["query"].get("updatedSince") for r in fake.requests if r["endpoint"] == "metadata"][-2:] == \
        ["2026-10-01T00:00:00.000Z"] * 2
    assert lib.removed == 1 and lib.rekeyed == [("n0001", "n0005")]
    rows = {r.node_id: r for r in connected.items("lib0manga")}
    assert set(rows) == {"n0003", "n0004", "n0005"}
    assert rows["n0003"].link_state == "NeedsReview" and rows["n0005"].trail == ["A2"]
    assert connected.sync_state("lib0manga").server_time == "2026-10-02T00:00:00.000Z"


def test_repeated_items_are_upserts(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["A"], updated="2026-10-04T12:00:00.000Z")]
    _sync(connected, client_factory, fake)
    _sync(connected, client_factory, fake)            # inclusive updatedSince: the same item again
    assert connected.item_count("lib0manga") == 1


def test_409_falls_back_to_one_full_sync(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["A"]), folder("n0002", ["B"])]
    fake.server_time = "2026-08-01T00:00:00.000Z"
    _sync(connected, client_factory, fake)
    fake.items["lib0manga"] = [folder("n0001", ["A"])]  # B vanished, but the removal pool has expired
    fake.window_start = "2026-09-01T00:00:00.000Z"
    fake.server_time = "2026-10-04T00:00:00.000Z"
    res = _sync(connected, client_factory, fake)
    lib = res.libraries[0]
    assert res.status == "ok" and lib.fell_back_to_full and lib.mode == "full" and lib.pruned == 1
    queries = [r["query"] for r in fake.requests if r["endpoint"] == "metadata"]
    assert queries[-2].get("updatedSince") == "2026-08-01T00:00:00.000Z" and "updatedSince" not in queries[-1]
    assert [r.node_id for r in connected.items("lib0manga")] == ["n0001"]
    assert connected.sync_state("lib0manga").server_time == "2026-10-04T00:00:00.000Z"


def test_interrupted_sync_keeps_the_previous_server_time(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder(f"n{i:04d}", [f"S{i}"]) for i in range(4)]
    fake.server_time = "2026-10-01T00:00:00.000Z"
    _sync(connected, client_factory, fake)
    fake.server_time = "2026-10-02T00:00:00.000Z"
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 1

    res = sync_all(connected, client=client_factory(fake.url), list_series=lambda rid: [], limit=2,
                   should_stop=stop)
    assert res.status == "error"
    assert connected.sync_state("lib0manga").server_time == "2026-10-01T00:00:00.000Z"


def test_401_stops_everything_and_scheduled_runs_skip_until_a_new_token(connected, fake, client_factory, caplog):
    caplog.set_level(logging.DEBUG)
    fake.libraries.append({"id": "lib1", "displayName": "Manhwa", "kind": "manhwa"})
    fake.items["lib1"] = []
    fake.token = "mpx_rotated"                         # the stored token was revoked
    res = _sync(connected, client_factory, fake)
    assert res.status == "error" and res.token_rejected
    assert len(fake.requests) == 1                     # nothing after the 401
    assert connected.connection().token_rejected_at
    res = sync_all(connected, client=client_factory(fake.url), list_series=lambda rid: [])
    assert res.status == "skipped" and len(fake.requests) == 1
    # "Sync now" (manual) tries once more; a new token lifts the stop.
    res = sync_all(connected, client=client_factory(fake.url), list_series=lambda rid: [], manual=True)
    assert res.token_rejected and len(fake.requests) == 2
    connected.set_connection(token="mpx_rotated")
    assert connected.connection().token_rejected_at is None
    res = sync_all(connected, client=client_factory(fake.url, token="mpx_rotated"), list_series=lambda rid: [])
    assert res.status == "ok"
    assert TOKEN not in caplog.text and "mpx_rotated" not in caplog.text


def test_401_in_the_middle_stops_at_once(connected, fake, client_factory):
    fake.libraries.append({"id": "lib1", "displayName": "Manhwa", "kind": "manhwa"})
    fake.items["lib0manga"] = [folder("n0001", ["A"])]
    fake.items["lib1"] = [folder("n0101", ["X"])]
    fake.queue.append(("metadata", 401, None, {}))
    res = _sync(connected, client_factory, fake)
    assert res.token_rejected and fake.count("metadata") == 1


def test_library_kinds_default_and_override(connected, fake, client_factory, db, tmp_path):
    fake.libraries = [
        {"id": "m", "displayName": "Manga", "kind": "manga"},
        {"id": "w", "displayName": "Webtoons", "kind": "webtoon"},
        {"id": "x", "displayName": "Untyped", "kind": None},
        {"id": "c", "displayName": "Comics", "kind": "comic"},
        {"id": "g", "displayName": "GN", "kind": "graphic-novel"},
        {"id": "n", "displayName": "Novels", "kind": "novel"},
    ]
    for lib in fake.libraries:
        fake.items[lib["id"]] = [folder(f"n{lib['id']}01", [lib["displayName"] + " Series"])]
    res = _sync(connected, client_factory, fake)
    assert sorted(s.library_id for s in res.libraries) == ["m", "w", "x"]
    assert connected.item_count("c") == 0
    from mangalist.services.mangapixer.mapping import set_manual_mapping

    root = db.add_root(str(tmp_path / "comics"), "Comics")
    set_manual_mapping(connected, root.id, "c", any_kind=True, list_series=lambda rid: [])
    res = _sync(connected, client_factory, fake)
    assert "c" in {s.library_id for s in res.libraries} and connected.item_count("c") == 1


def test_library_not_found_marks_it_gone(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["A"])]
    _sync(connected, client_factory, fake)
    fake.libraries = []
    res = _sync(connected, client_factory, fake)
    # /libraries no longer lists it -> nothing to sync; the cache stays until the owner re-maps.
    assert res.libraries == [] and not connected.library("lib0manga").present
    fake.libraries = [{"id": "lib0manga", "displayName": "Manga", "kind": "manga"}]
    fake.queue.append(("metadata", 404, {"error": "libraryNotFound"}, {}))
    res = _sync(connected, client_factory, fake)
    assert res.status == "error" and "re-map" in res.libraries[0].error
    assert connected.sync_state("lib0manga").last_status == "error"


def test_schema_version_2_stops_the_sync(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["A"])]
    _sync(connected, client_factory, fake)
    fake.schema_version = 2
    res = _sync(connected, client_factory, fake)
    assert res.status == "error" and "version 2" in res.message
    assert connected.item_count("lib0manga") == 1    # nothing replaced by an answer we cannot read


def test_unknown_provider_is_kept_but_not_used(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["A"], provider="gcd")]
    _sync(connected, client_factory, fake)
    item = connected.item("lib0manga", "n0001")
    assert item["record"]["provider"] == "gcd" and mpc.usable_record(item) is None


def test_force_full_and_unconfigured(cache, fake, client_factory):
    assert sync_all(cache, list_series=lambda rid: []).status == "skipped"
    cache.set_connection(base_url=fake.url, token=TOKEN)
    fake.items["lib0manga"] = [folder("n0001", ["A"])]
    _sync(cache, client_factory, fake)
    _sync(cache, client_factory, fake, force_full=True)
    assert "updatedSince" not in fake.requests[-1]["query"]


def test_progress_reports(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder(f"n{i:04d}", [f"S{i}"]) for i in range(5)]
    seen = []
    lib = connected.save_libraries(client_factory(fake.url).libraries())[0]
    sync_library(connected, client_factory(fake.url), lib, limit=2, progress=lambda d, t, n: seen.append((d, t, n)))
    assert seen == [(2, 4, "Manga"), (4, 4, "Manga"), (5, 5, "Manga")]


def test_stored_rows_hold_no_paths_only_trails(connected, fake, client_factory):
    fake.items["lib0manga"] = [folder("n0001", ["Shonen", "Series One"])]
    _sync(connected, client_factory, fake)
    with connected.connect() as con:
        r = con.execute("SELECT trail, trail_key FROM mangapixer_items").fetchone()
    assert json.loads(r["trail"]) == ["Shonen", "Series One"] and r["trail_key"] == "Shonen/Series One"
