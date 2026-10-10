"""Logging controls: the level and per-area levels, applied at once; rotation; the stored settings; secrets never
reach a line. Every test restores the global logging state (the suite runs in parallel and other tests read log
output through caplog)."""

from __future__ import annotations

import logging
import logging.handlers

import pytest

from mangalist import log_config, paths, store
from mangalist.log_config import LogSettings


@pytest.fixture
def logs(tmp_path):
    """log_config set up on the test's own data folder, with the root logger put back afterwards."""
    root = logging.getLogger()
    saved = (list(root.handlers), root.level, logging.getLogger("urllib3").level)
    log_config.shutdown()
    yield log_config
    log_config.shutdown()
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])
    logging.getLogger("urllib3").setLevel(saved[2])
    for name in ("mangalist.scanner", "mangalist.services.nyaa.client", "mangalist.duplicates"):
        logging.getLogger(name).setLevel(logging.NOTSET)


def _text(logs_module=log_config) -> str:
    for h in logging.getLogger().handlers:
        h.flush()
    path = paths.log_dir() / "mangalist.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


@pytest.fixture
def db():
    store.reset_stores()
    return store.get_store()


def test_default_is_info_to_the_file_and_warning_to_the_console(logs):
    logs.setup()
    log = logging.getLogger("mangalist.scanner")
    log.debug("debug detail")
    log.info("scan done")
    text = _text()
    assert "scan done" in text and "debug detail" not in text
    console = [h for h in logging.getLogger().handlers if type(h) is logging.StreamHandler][0]
    assert console.level == logging.WARNING, "the console behaves as before"
    assert logs.current().level == "info"


def test_the_runner_uses_its_own_file_and_info_on_standard_output(logs, capsys):
    from mangalist.headless import runner

    runner.setup_logging()
    logging.getLogger("mangalist.headless").info("runner line")
    logging.getLogger("mangalist.headless").debug("hidden")
    for h in logging.getLogger().handlers:
        h.flush()
    assert "runner line" in capsys.readouterr().out
    assert "runner line" in (paths.log_dir() / "headless.log").read_text(encoding="utf-8")
    assert not (paths.log_dir() / "mangalist.log").exists()


def test_a_change_applies_at_once_without_a_restart(logs):
    logs.setup()
    log = logging.getLogger("mangalist.scanner")
    log.debug("before")
    logs.apply(LogSettings(level="debug"))
    log.debug("while debug")
    logs.apply(LogSettings(level="error"))
    log.warning("while error: a warning")
    log.error("while error: an error")
    text = _text()
    assert "before" not in text and "while debug" in text
    assert "a warning" not in text and "an error" in text


def test_an_area_level_overrides_the_general_one_for_its_loggers_only(logs):
    logs.setup()
    logs.apply(LogSettings(level="warning", areas={"scan": "debug", "nyaa": "error"}))
    logging.getLogger("mangalist.scanner").debug("scanner detail")
    logging.getLogger("mangalist.services.nyaa.client").warning("nyaa warning")
    logging.getLogger("mangalist.services.mangapixer.sync").debug("mangapixer detail")
    logging.getLogger("mangalist.services.mangapixer.sync").warning("mangapixer warning")
    text = _text()
    assert "scanner detail" in text and "mangapixer warning" in text
    assert "nyaa warning" not in text and "mangapixer detail" not in text


def test_the_longest_prefix_wins_and_a_prefix_is_a_whole_name_part(logs):
    f = log_config.LevelFilter()
    f.configure(LogSettings(level="info", areas={"scan": "debug", "mangapixer": "error"}))
    assert f.level_for("mangalist.identity.backfill") == logging.DEBUG          # scanning area
    assert f.level_for("mangalist.identity.mangapixer") == logging.ERROR        # the longer prefix: MangaPixer
    assert f.level_for("mangalist.scanner.extra") == logging.DEBUG
    assert f.level_for("mangalist.scannerx") == logging.INFO, "'mangalist.scanner' does not match 'scannerx'"
    assert f.level_for("urllib3.connectionpool") == logging.INFO


def test_rotation_settings_apply_to_the_open_handler(logs):
    logs.setup()
    logs.apply(LogSettings(max_mb=7, backups=3))
    handler = [h for h in logging.getLogger().handlers
               if isinstance(h, logging.handlers.RotatingFileHandler)][0]
    assert (handler.maxBytes, handler.backupCount) == (7 * 1024 * 1024, 3)


def test_urllib3_stays_quiet_at_every_level(logs):
    logs.setup()
    logs.apply(LogSettings(level="debug"))
    assert logging.getLogger("urllib3").level == logging.WARNING


def test_settings_round_trip_and_garbage_falls_back_to_the_defaults(db):
    saved = log_config.save(db, LogSettings(level="DEBUG", areas={"nyaa": "warn", "nonsense": "debug", "scan": "x"},
                                            max_mb=500, backups=0))
    assert saved == LogSettings("debug", {"nyaa": "warning"}, 50, 1), "clamped, unknown areas and levels dropped"
    assert log_config.load(db) == saved
    db.set_settings({"log_level": "loud", "log_areas": "nope", "log_max_mb": "big", "log_backups": None})
    assert log_config.load(db) == LogSettings()


def test_the_environment_only_seeds_the_level(db):
    env = {"MANGALIST_LOG_LEVEL": "debug"}
    assert log_config.load(db, env).level == "debug"             # nothing stored: the variable is the first-run default
    log_config.save(db, LogSettings(level="warning"))
    assert log_config.load(db, env).level == "warning"           # the owner's choice beats it
    assert log_config.load(db, {"MANGALIST_LOG_LEVEL": "shouting"}).level == "warning"
    db.delete_setting("log_level")
    assert log_config.load(db, {"MANGALIST_LOG_LEVEL": "shouting"}).level == "info"


def test_apply_stored_reads_the_database_and_never_raises(logs, db):
    logs.setup()
    log_config.save(db, LogSettings(level="debug"))
    assert logs.apply_stored(db).level == "debug" and logs.current().level == "debug"

    class Broken:
        def get_setting(self, *a, **k):
            raise OSError("disk gone")

    assert logs.apply_stored(Broken()) is None
    assert logs.current().level == "debug", "the level in force is kept"


def test_apply_before_setup_does_nothing(logs):
    assert logs.apply(LogSettings(level="debug")).level == "debug" and logs.current() is None


# --- secrets ----------------------------------------------------------------------------------------------------

TOKEN = "mpx_SecretTokenDoNotShow_0123456789"
PASSWORD = "hunter2-example"
COOKIE = "SID=abcdef0123456789"


@pytest.mark.parametrize("line", [
    f"GET failed, headers {{'Authorization': 'Bearer {TOKEN}'}}",
    f"Authorization: Bearer {TOKEN}",
    f"retrying with Bearer {TOKEN}",
    f"https://example.invalid/api?token={TOKEN}&x=1",
    f"login {{'username': 'u', 'password': '{PASSWORD}'}}",
    f"password={PASSWORD}",
    f"cookie: {COOKIE}",
    f"Set-Cookie: {COOKIE}; HttpOnly",
    f"api_key={TOKEN}",
])
def test_redact_masks_the_usual_shapes_of_a_secret(line):
    out = log_config.redact(line)
    assert TOKEN not in out and PASSWORD not in out and "abcdef0123456789" not in out
    assert "***" in out


def test_redact_leaves_ordinary_lines_alone():
    line = "Rescan: /data/manga - 12 series, 340 archives in 4.2 s; next run 2026-10-10 03:30"
    assert log_config.redact(line) == line


def test_a_secret_in_a_message_or_a_traceback_never_reaches_the_file(logs):
    logs.setup()
    log = logging.getLogger("mangalist.services.mangapixer.client")
    log.warning("request failed: Authorization: Bearer %s", TOKEN)
    try:
        raise RuntimeError(f"boom with password={PASSWORD}")
    except RuntimeError:
        log.exception("sync failed")
    text = _text()
    assert "request failed" in text and "sync failed" in text
    assert TOKEN not in text and PASSWORD not in text
