"""Job registry, the roots provider, the rescan job and the opt-in (default off) dispatch."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from mangalist.headless.jobs import (
    Cancelled, ConfigRootsProvider, Job, JobContext, JobRegistry, RootsProvider, StoreRootsProvider,
    build_registry, make_dispatch, make_rescan)
from mangalist.store.roots import Root
from mangalist.headless.schedule import DailyAt, EveryHours
from mangalist.headless.settings import HeadlessSettings, parse_bool


class ListRoots:
    def __init__(self, *roots):
        self._roots = [Path(r) for r in roots]

    def roots(self):
        return list(self._roots)


def _library(tmp_path: Path) -> Path:
    root = tmp_path / "library"
    for series, files in {"Series A": ["Series A v01.cbz", "Series A v02.cbz"],
                          "Series B": ["Series B c001.cbz"]}.items():
        (root / series).mkdir(parents=True)
        for f in files:
            (root / series / f).write_bytes(b"PK")
    return root


# --- registry -----------------------------------------------------------------------------------

def test_registry_names_get_and_duplicates():
    reg = JobRegistry([Job("a", lambda c: None, None), Job("b", lambda c: None, EveryHours(1))])
    assert reg.names() == ["a", "b"] and "a" in reg
    assert [j.name for j in reg.active()] == ["b"]
    with pytest.raises(ValueError):
        reg.add(Job("a", lambda c: None, None))
    with pytest.raises(KeyError, match="unknown job 'zzz'"):
        reg.get("zzz")


def test_default_settings_rescan_on_downloads_off():
    s = HeadlessSettings.from_env({})
    assert s.downloads_enabled is False
    assert s.rescan_schedule == DailyAt(3, 30)
    assert s.catch_up is True
    reg = build_registry(s, ListRoots())
    assert reg.names() == ["rescan", "dispatch-batch"]
    assert reg.get("rescan").active
    assert not reg.get("dispatch-batch").enabled
    assert [j.name for j in reg.active()] == ["rescan"]


def test_downloads_are_opt_in():
    s = HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "1",
                                   "MANGALIST_DISPATCH_SCHEDULE": "every 12h"})
    reg = build_registry(s, ListRoots())
    assert reg.get("dispatch-batch").active
    assert reg.get("dispatch-batch").schedule == EveryHours(12)


def test_rescan_can_be_turned_off():
    reg = build_registry(HeadlessSettings.from_env({"MANGALIST_RESCAN_SCHEDULE": "off"}),
                         ListRoots())
    assert reg.active() == []


def test_bad_settings_are_errors_not_surprises():
    with pytest.raises(ValueError):
        HeadlessSettings.from_env({"MANGALIST_RESCAN_SCHEDULE": "nightly"})
    with pytest.raises(ValueError):
        HeadlessSettings.from_env({"MANGALIST_DOWNLOADS": "maybe"})
    assert parse_bool(None, True) is True and parse_bool("off", True) is False


def test_tz_from_env_and_unknown_tz_falls_back():
    from .conftest import zone

    zone("Europe/Berlin")
    assert getattr(HeadlessSettings.from_env({"TZ": "Europe/Berlin"}).tz, "key", "") == \
        "Europe/Berlin"
    assert HeadlessSettings.from_env({"TZ": "Not/AZone"}).tz is not None


# --- dispatch -----------------------------------------------------------------------------------

def test_dispatch_off_dispatches_nothing(caplog):
    caplog.set_level(logging.INFO)
    result = make_dispatch(False)(JobContext())
    assert result.status == "skipped" and "off" in result.message
    assert "nothing dispatched" in caplog.text


def test_dispatch_on_is_still_a_placeholder(caplog):
    caplog.set_level(logging.INFO)
    result = make_dispatch(True)(JobContext())
    assert result.status == "skipped" and "not implemented" in result.message
    assert "nothing dispatched" in caplog.text


# --- roots --------------------------------------------------------------------------------------

def test_config_roots_provider_reads_env_and_last_root(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    provider = ConfigRootsProvider(env={"MANGALIST_ROOTS": os.pathsep.join([str(a), str(b), ""])},
                                   load_config=lambda: {"last_root": str(a)})
    assert isinstance(provider, RootsProvider)
    assert provider.roots() == [a, b], "deduplicated, env order first"


def test_config_roots_provider_uses_the_real_config(tmp_path):
    from mangalist import config

    cfg = config.load()
    cfg["last_root"] = str(tmp_path)
    config.save(cfg)
    assert ConfigRootsProvider(env={}).roots() == [tmp_path]


def test_config_roots_provider_survives_a_broken_config():
    def broken():
        raise RuntimeError("bad")

    assert ConfigRootsProvider(env={}, load_config=broken).roots() == []


# --- rescan -------------------------------------------------------------------------------------

def test_rescan_scans_every_root_with_the_real_scanner(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    root = _library(tmp_path)
    result = make_rescan(ListRoots(root))(JobContext())
    assert result.status == "ok", result
    (summary,) = result.extra["roots"]
    assert summary["series"] == 2 and summary["archives"] == 3
    assert sum(summary["verdicts"].values()) == 2
    assert "2 series, 3 archives" in caplog.text


def test_rescan_without_roots_is_skipped():
    assert make_rescan(ListRoots())(JobContext()).status == "skipped"


def test_rescan_reports_a_missing_root_and_scans_the_others(tmp_path):
    root = _library(tmp_path)
    result = make_rescan(ListRoots(tmp_path / "not-mounted", root))(JobContext())
    assert result.status == "error"
    assert result.message == "1 of 2 roots scanned"
    assert result.extra["roots"][0]["error"] == "not a folder"
    assert result.extra["roots"][1]["series"] == 2


def test_rescan_stops_on_shutdown(tmp_path):
    root = _library(tmp_path)
    seen = []

    def fake_scan(path, progress=None):
        seen.append(path)
        progress(0, 2, "x")
        return []

    stop = {"now": False}
    rescan = make_rescan(ListRoots(root, root / "Series A"), scan=fake_scan)
    stop["now"] = True
    with pytest.raises(Cancelled):
        rescan(JobContext(lambda: stop["now"]))
    assert seen == [], "checked before the first root"

    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 1        # stop during the first root's progress callback

    with pytest.raises(Cancelled):
        rescan(JobContext(should_stop))
    assert seen == [root]


# --- the roots database as the provider ---------------------------------------------------------

def test_store_provider_lists_database_roots_then_environment_roots(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    stored = Root(id=1, name="A", path=str(a), exclusions=["@Oneshots"])
    provider = StoreRootsProvider(env={"MANGALIST_ROOTS": os.pathsep.join([str(a), str(b)])},
                                  load_roots=lambda: [stored])
    roots = provider.roots()
    # The database root wins over the same path from the environment (it carries the exclusions).
    assert roots == [stored, b]
    assert isinstance(provider, RootsProvider)


def test_store_provider_survives_a_broken_database(tmp_path):
    def broken():
        raise RuntimeError("database locked")

    provider = StoreRootsProvider(env={"MANGALIST_ROOTS": str(tmp_path)}, load_roots=broken)
    assert provider.roots() == [tmp_path]


def test_rescan_never_scans_a_database_roots_exclusions(tmp_path):
    root = _library(tmp_path)
    (root / "@Oneshots").mkdir()
    (root / "@Oneshots" / "One Shot.cbz").write_bytes(b"PK")
    stored = Root(id=1, name="Library", path=str(root), exclusions=["@Oneshots"])
    result = make_rescan(StoreRootsProvider(env={}, load_roots=lambda: [stored]))(JobContext())
    assert result.status == "ok", result
    (summary,) = result.extra["roots"]
    assert summary["series"] == 2 and summary["archives"] == 3  # @Oneshots not scanned

    # The same root as a plain path (no database) does scan the folder.
    plain = make_rescan(ListRoots(root))(JobContext())
    assert plain.extra["roots"][0]["series"] == 3


def test_build_registry_defaults_to_the_roots_database():
    reg = build_registry(HeadlessSettings.from_env({}))
    assert reg.get("rescan").active
