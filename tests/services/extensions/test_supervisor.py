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
    def __init__(self, args, env=None):
        self.args = args
        self.env = env
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

    def spawn(args, env=None, **_streams):
        spawned.append(FakeProcess(args, env))
        return spawned[-1]

    clock = Clock()
    sup = sv.Supervisor(tmp_path / "config", spawn=spawn, clock=clock, env=env or {}, open_settings=lambda: settings)
    return sup, spawned, clock


def test_the_command_line_from_the_settings_and_the_environment(tmp_path):
    config = ExtensionHostConfig(url="http://127.0.0.1:4571")
    env = {"MANGALIST_FLARESOLVERR_URL": "http://192.168.2.50:8191"}
    cmd = sv.host_command(config, Path("/config"), env=env)
    data = Path("/config") / "extension-host"          # the platform's separators (CI runs this on Windows too)
    assert cmd == (BIN, "--data", str(data), "--bind", "127.0.0.1", "--port", "4571",
                   "--downloads", str(data / "downloads"), "--webview", "on",
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

    def spawn(args, env=None, **_streams):
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


def test_the_host_starts_in_a_utf8_locale(tmp_path):
    """The image's LANG names a locale it does not have, so the host's Java fell back to ASCII file names and a chapter
    by "Les Poroïniens" could not be written (owner's container, 2026-10-24)."""
    env = {"LANG": "en_US.UTF-8", "PATH": "/usr/bin", "MANGALIST_FLARESOLVERR_URL": "http://192.168.2.50:8191"}
    out = sv.host_env(env)
    assert out["LANG"] == out["LC_ALL"] == "C.UTF-8" and out["PATH"] == "/usr/bin"
    assert out["MANGALIST_FLARESOLVERR_URL"] == "http://192.168.2.50:8191" and env["LANG"] == "en_US.UTF-8"


def test_the_supervisor_spawns_the_host_with_the_utf8_environment(tmp_path, settings):
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup, spawned, _clock = make(tmp_path, settings, env={"LANG": "en_US.UTF-8"})
    sup.tick()
    assert len(spawned) == 1 and spawned[0].env["LC_ALL"] == spawned[0].env["LANG"] == "C.UTF-8"


# --- the transition fixes (2026-10-24) -------------------------------------------------------------------------------

def test_on_demand_while_suwayomi_is_chosen_and_stopped_again_after(tmp_path, settings):
    """Audit C1: the Extensions page, a test or a carry-over asks for the host; 30 minutes after, it stops."""
    from datetime import datetime, timedelta, timezone

    from mangalist.services.extensions import on_demand

    sup, spawned, _clock = make(tmp_path, settings)
    sup.tick()
    assert spawned == []
    on_demand.request(settings.store)
    sup.tick()
    assert len(spawned) == 1 and sup.last_why.startswith("on demand")
    past = datetime.now(timezone.utc) - timedelta(minutes=on_demand.ON_DEMAND_MINUTES + 1)
    settings.store.set_meta(on_demand.META_UNTIL, past.isoformat())
    sup.tick()
    assert spawned[0].terminated and sup.process is None


def test_runs_while_a_chapter_download_of_the_host_is_to_be_finished(tmp_path, settings):
    """Audit C2: with Suwayomi chosen again, a host record still filed (its copy not deleted) is finished by the host."""
    with settings.store.connect() as con:
        con.execute("INSERT INTO ledger (created_at, updated_at, request, tool, status, external_ref, backend)"
                    " VALUES ('t', 't', '{}', 'suwayomi', 'filed', '5', 'extensions')")
    sup, spawned, _clock = make(tmp_path, settings)
    sup.tick()
    assert len(spawned) == 1 and "1 chapter download(s) of the host" in sup.last_why
    with settings.store.connect() as con:
        con.execute("UPDATE ledger SET status = 'removed'")
    sup.tick()
    assert spawned[0].terminated


def test_the_downloads_folder_on_the_data_share_when_it_is_there(tmp_path, settings):
    """Audit B6: the image names a folder on /data (hard links into the library); used when it or its parent exists."""
    share = tmp_path / "share" / "appdata" / "mangalist"
    wanted = share / "extension-host-downloads"
    env = {sv.ENV_DOWNLOADS: str(wanted)}
    assert sv.downloads_folder(tmp_path / "config", env) == tmp_path / "config" / "extension-host" / "downloads"
    share.mkdir(parents=True)
    assert sv.downloads_folder(tmp_path / "config", env) == wanted
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup, spawned, _clock = make(tmp_path, settings, env=env)
    sup.tick()
    args = spawned[0].args
    assert args[args.index("--downloads") + 1] == str(wanted) and wanted.is_dir()
    assert settings.extension_host().downloads_path == str(wanted)


def test_the_downloads_folder_moves_only_once_the_old_one_holds_no_chapter(tmp_path, settings):
    old = tmp_path / "config" / "extension-host" / "downloads"
    cbz = old / "mangas" / "MangaDex (EN)" / "Title" / "Ch.1.cbz"
    cbz.parent.mkdir(parents=True)
    cbz.write_bytes(b"x")
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    settings.learn_downloads_path(str(old))
    (tmp_path / "share").mkdir()
    env = {sv.ENV_DOWNLOADS: str(tmp_path / "share" / "dl")}
    want = sv.wanted(tmp_path / "config", env=env, open_settings=lambda: settings)
    assert want.downloads == str(old) and "moves to" in want.why
    cbz.unlink()
    want = sv.wanted(tmp_path / "config", env=env, open_settings=lambda: settings)
    assert want.downloads == str(tmp_path / "share" / "dl")


def test_the_hosts_output_goes_into_the_log_as_it_is(tmp_path, caplog):
    """Audit D2: the host's lines (already in MangaList's format) reach the log file and the in-app viewer."""
    import io
    import logging

    stream = io.BytesIO("2026-10-24 11:20:00  INFO      mangalist.extensionhost.Main  [main] Poroïniens\n\n".encode())
    with caplog.at_level(logging.INFO, logger="mangalist.extension_host.output"):
        sv.pump(stream)
    (record,) = caplog.records
    assert record.raw and record.getMessage().endswith("Poroïniens")
    inner = logging.Formatter("%(levelname)s %(message)s")
    raw_aware = sv.RawAware(inner)
    assert raw_aware.format(record) == record.getMessage()
    plain = logging.LogRecord("mangalist.extension_host", logging.INFO, "", 0, "Extension host: started", (), None)
    assert raw_aware.format(plain) == "INFO Extension host: started"


def test_the_log_file_is_the_viewers(tmp_path, monkeypatch):
    from mangalist.gui import log_tail

    assert log_tail.LOG_FILE["host"] == sv.LOG_FILE
    for logger in ("mangalist.extensionhost.Downloader", "mangalist.extension_host", "suwayomi.tachidesk.manga.impl",
                   "mangalist.services.extensions.compat", "mangalist.downloads.extensions_backend",
                   "mangalist.downloads.host_identity", "eu.kanade.tachiyomi.network.NetworkHelper"):
        assert log_tail.area_of(logger) == "Extensions", logger
    line = "2026-10-24 11:20:00  WARNING   mangalist.extensionhost.Downloader  [w-1] Chapter 1 failed (try 1 of 3)"
    assert log_tail.HEADER.match(line)


def test_the_downloads_folder_is_learned_once_the_database_exists(tmp_path, settings):
    """The container's first start: the host runs before MangaList made its database; a later poll stores the folder."""
    there = {"db": None}
    sup = sv.Supervisor(tmp_path / "config", spawn=lambda args, env=None, **_s: FakeProcess(args, env), clock=Clock(),
                        env={sv.ENV_SWITCH: "1"}, open_settings=lambda: there["db"])
    sup.tick()
    assert sup.process is not None and sup.learned == ""
    there["db"] = settings
    sup.tick()
    assert settings.extension_host().downloads_path == str(tmp_path / "config" / "extension-host" / "downloads")


# --- the Host downloads folder in Settings (owner-approved 2026-10-24): Settings > the env var > the default ----------

def test_the_downloads_folder_precedence_is_settings_then_the_env_var_then_the_default(tmp_path):
    config = tmp_path / "config"
    default = config / "extension-host" / "downloads"
    share = tmp_path / "share"
    share.mkdir()
    from_env, from_settings = share / "from-env", share / "from-settings"
    env = {sv.ENV_DOWNLOADS: str(from_env)}
    assert sv.downloads_folder(config, {}) == default
    assert sv.downloads_folder(config, env) == from_env
    assert sv.downloads_folder(config, env, str(from_settings)) == from_settings          # Settings wins
    assert sv.downloads_folder(config, {}, str(from_settings)) == from_settings           # without the env var too
    assert sv.downloads_folder(config, env, "") == from_env and sv.downloads_folder(config, env, "  ") == from_env
    # a Settings folder that cannot be used (neither it nor its parent exists) falls through to the env var / default
    missing = tmp_path / "nowhere" / "deeper" / "dl"
    assert sv.downloads_folder(config, env, str(missing)) == from_env
    assert sv.downloads_folder(config, {}, str(missing)) == default


def test_the_settings_folder_is_what_the_host_is_started_with_and_a_change_restarts_it(tmp_path, settings):
    share = tmp_path / "share"
    share.mkdir()
    env = {sv.ENV_DOWNLOADS: str(share / "from-env")}
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    sup, spawned, _clock = make(tmp_path, settings, env=env)
    sup.tick()
    args = spawned[0].args
    assert args[args.index("--downloads") + 1] == str(share / "from-env")
    settings.save_extension_host(ExtensionHostConfig(host_downloads=str(share / "from-settings")))
    sup.tick()
    assert spawned[0].terminated                                   # the host restarts to use it
    sup.tick()
    args = spawned[-1].args
    assert args[args.index("--downloads") + 1] == str(share / "from-settings")
    assert settings.extension_host().host_downloads == str(share / "from-settings")


def test_an_unusable_settings_folder_is_said_and_the_env_var_is_used(tmp_path, settings):
    share = tmp_path / "share"
    share.mkdir()
    settings.set_backend(CHAPTER_BACKEND_EXTENSIONS)
    settings.save_extension_host(ExtensionHostConfig(host_downloads=str(tmp_path / "nowhere" / "x" / "dl")))
    want = sv.wanted(tmp_path / "config", env={sv.ENV_DOWNLOADS: str(share / "dl")}, open_settings=lambda: settings)
    assert want.downloads == str(share / "dl") and "do not exist" in want.why
