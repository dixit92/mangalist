"""The container's extension host supervisor (:mod:`mangalist.services.extensions.supervisor`): the host runs while
"Built-in extensions" is chosen (or always / never by the switch), with its options from MangaList's settings or the
environment, restarted when they change, started again when it exits - with a pause when it keeps failing. A fake
process stands in for the JVM."""

from __future__ import annotations

from pathlib import Path

import pytest

from mangalist import store
from mangalist.downloads.contracts import CHAPTER_BACKEND_EXTENSIONS, CHAPTER_BACKEND_SUWAYOMI
from mangalist.services.extensions import supervisor as sv
from mangalist.store.downloads import ExtensionHostConfig, SuwayomiSettings

BIN = "/opt/mangalist/extension-host/bin/mangalist-extension-host"


class FakeProcess:
    def __init__(self, args):
        self.args = args
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 143

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = -9


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def settings(tmp_path) -> SuwayomiSettings:
    store.reset_stores()
    return SuwayomiSettings(store.get_store())


def make(tmp_path, settings, env=None):
    spawned = []

    def spawn(args):
        spawned.append(FakeProcess(args))
        return spawned[-1]

    clock = Clock()
    sup = sv.Supervisor(tmp_path / "config", spawn=spawn, clock=clock, env=env or {}, open_settings=lambda: settings)
    return sup, spawned, clock


def test_the_command_line_from_the_settings_and_the_environment(tmp_path):
    config = ExtensionHostConfig(url="http://127.0.0.1:4571")
    env = {"MANGALIST_FLARESOLVERR_URL": "http://192.168.2.50:8191"}
    cmd = sv.host_command(config, Path("/config"), env=env)
    assert cmd == (BIN, "--data", "/config/extension-host", "--bind", "127.0.0.1", "--port", "4571",
                   "--downloads", "/config/extension-host/downloads", "--webview", "on",
                   "--flaresolverr", "http://192.168.2.50:8191")
    mine = ExtensionHostConfig(flaresolverr_url="http://flare:8191", repo_url="https://repo.example/index.json",
                               webview=False)
    cmd = sv.host_command(mine, Path("/config"), env={**env, "MANGALIST_EXTENSION_REPO": "https://other/index.json"})
    assert cmd[cmd.index("--flaresolverr") + 1] == "http://flare:8191"          # the setting wins
    assert cmd[cmd.index("--repo") + 1] == "https://repo.example/index.json"
    assert cmd[cmd.index("--webview") + 1] == "off" and cmd[cmd.index("--port") + 1] == "4570"
    off = sv.host_command(ExtensionHostConfig(), Path("/config"), env={"MANGALIST_EXTENSION_WEBVIEW": "0"})
    assert off[off.index("--webview") + 1] == "off" and "--flaresolverr" not in off and "--repo" not in off


@pytest.mark.parametrize("value, mode", [("", "auto"), ("auto", "auto"), ("1", "on"), ("0", "off"), ("no", "off")])
def test_the_switch(value, mode):
    assert sv.switch({sv.ENV_SWITCH: value} if value else {}) == mode


def test_runs_only_while_built_in_extensions_are_chosen(tmp_path, settings):
    sup, spawned, _clock = make(tmp_path, settings)
    sup.tick()
    assert spawned == [] and sup.last_why == "chapter downloads go through Suwayomi"
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup.tick()
    assert len(spawned) == 1 and spawned[0].args[0] == BIN
    assert (tmp_path / "config" / "extension-host" / "downloads").is_dir()
    # the folder MangaList files from is known from the start
    assert settings.extension_host().downloads_path == str(tmp_path / "config" / "extension-host" / "downloads")
    sup.tick()
    assert len(spawned) == 1                                   # running: nothing to do
    settings.set_backend(CHAPTER_BACKEND_SUWAYOMI)
    sup.tick()
    assert spawned[0].terminated and sup.process is None


def test_a_changed_option_restarts_the_host(tmp_path, settings):
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup, spawned, _clock = make(tmp_path, settings)
    sup.tick()
    settings.save_extension_host(ExtensionHostConfig(flaresolverr_url="http://192.168.2.50:8191"))
    sup.tick()
    assert spawned[0].terminated and len(spawned) == 2 and "--flaresolverr" in spawned[1].args


def test_an_exited_host_is_started_again_with_a_pause_when_it_keeps_failing(tmp_path, settings):
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup, spawned, clock = make(tmp_path, settings)
    sup.tick()
    spawned[-1].returncode = 1                                 # crashed after a long run: at once
    clock.now += 600
    sup.tick()
    assert len(spawned) == 2
    spawned[-1].returncode = 1                                 # then twice quickly: a pause
    clock.now += 5
    sup.tick()
    assert len(spawned) == 3
    spawned[-1].returncode = 1
    clock.now += 5
    sup.tick()
    assert len(spawned) == 3 and sup.not_before > clock.now
    clock.now += sv.BACKOFF_SECONDS
    sup.tick()
    assert len(spawned) == 4


def test_always_on_and_off_by_the_switch(tmp_path, settings):
    sup, spawned, _clock = make(tmp_path, settings, env={sv.ENV_SWITCH: "1"})
    sup.tick()
    assert len(spawned) == 1                                   # Suwayomi chosen, but forced on
    sup, spawned, _clock = make(tmp_path, settings, env={sv.ENV_SWITCH: "0"})
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup.tick()
    assert spawned == []


def test_no_database_yet_means_not_running(tmp_path, monkeypatch):
    store.reset_stores()
    monkeypatch.setenv("MANGALIST_DATA_DIR", str(tmp_path / "fresh"))
    want = sv.wanted(tmp_path / "fresh", env={})
    assert not want.run and not (tmp_path / "fresh" / "mangalist.db").exists()


def test_a_binary_that_cannot_start_waits(tmp_path, settings):
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)

    def spawn(args):
        raise FileNotFoundError(args[0])

    clock = Clock()
    sup = sv.Supervisor(tmp_path, spawn=spawn, clock=clock, env={}, open_settings=lambda: settings)
    sup.tick()
    assert sup.process is None and sup.not_before == clock.now + sv.BACKOFF_SECONDS


def test_the_settings_are_read_without_migrating_the_database(tmp_path, monkeypatch):
    # three processes creating the database at the container's first start raced ("duplicate column name", 2026-10-22):
    # the supervisor only reads the settings table, and only once it exists
    import sqlite3

    data = tmp_path / "cfg"
    data.mkdir()
    monkeypatch.setenv("MANGALIST_DATA_DIR", str(data))
    db = data / "mangalist.db"
    sqlite3.connect(db).close()                                # being created: no settings table yet
    assert sv._open_settings(data) is None
    assert sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0] == 0     # nothing migrated
    store.reset_stores()
    real = SuwayomiSettings(store.get_store(db))
    real.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    real.save_extension_host(ExtensionHostConfig(url="http://127.0.0.1:4571"))
    mine = sv._open_settings(data)
    assert mine.builtin and mine.extension_host().url == "http://127.0.0.1:4571"
    mine.learn_downloads_path("/config/extension-host/downloads")
    assert real.extension_host().downloads_path == "/config/extension-host/downloads"
    assert sv.wanted(data, env={}).run


# --- several repositories (lane U, 2026-10-22) -----------------------------------------------------------------------

def test_every_repository_of_the_list_is_passed_and_the_environment_may_name_several(tmp_path):
    mine = ExtensionHostConfig(repos=("https://a.example/index.json", "https://b.example/index.json"))
    cmd = sv.host_command(mine, Path("/config"), env={"MANGALIST_EXTENSION_REPO": "https://env/index.json"})
    assert [cmd[i + 1] for i, part in enumerate(cmd) if part == "--repo"] == [
        "https://a.example/index.json", "https://b.example/index.json"]               # the list wins
    emptied = sv.host_command(ExtensionHostConfig(repos=()), Path("/config"),
                              env={"MANGALIST_EXTENSION_REPO": "https://env/index.json"})
    assert "--repo" not in emptied                     # the owner removed them all: the host's own list stays as it is
    env = sv.host_command(ExtensionHostConfig(), Path("/config"),
                          env={"MANGALIST_EXTENSION_REPO": "https://one/index.json, https://two/index.json"})
    assert [env[i + 1] for i, part in enumerate(env) if part == "--repo"] == ["https://one/index.json",
                                                                              "https://two/index.json"]
    assert sv.without_repos(cmd) == sv.without_repos(emptied)


def test_a_changed_repository_list_alone_does_not_restart_the_host(tmp_path, settings):
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup, spawned, _clock = make(tmp_path, settings)
    sup.tick()
    # added through the host's API from Settings: the running host has it already
    settings.save_extension_host(ExtensionHostConfig(repos=("https://a.example/index.json",)))
    sup.tick()
    assert len(spawned) == 1 and not spawned[0].terminated
    assert "https://a.example/index.json" in sup.command            # the next start passes it
    settings.save_extension_host(ExtensionHostConfig(repos=("https://a.example/index.json",), webview=False))
    sup.tick()
    assert spawned[0].terminated and len(spawned) == 2
    assert spawned[1].args[spawned[1].args.index("--repo") + 1] == "https://a.example/index.json"
