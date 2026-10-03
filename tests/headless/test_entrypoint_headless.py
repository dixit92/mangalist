"""``python -m mangalist --headless``: no Qt on that path, CLI options, SIGTERM shutdown."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

# Blocks any PySide6 import, so the check holds even where PySide6 is installed.
_NO_QT = (
    "import sys\n"
    "class _Block:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name == 'PySide6' or name.startswith('PySide6.'):\n"
    "            raise ImportError('PySide6 imported on the headless path: ' + name)\n"
    "        return None\n"
    "sys.meta_path.insert(0, _Block())\n"
)


def _env(tmp_path: Path, **extra) -> dict:
    env = dict(os.environ)
    env.update({"MANGALIST_DATA_DIR": str(tmp_path / "data"), "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(REPO)})
    env.pop("MANGALIST_ROOTS", None)
    env.update(extra)
    return env


def _run(code: str, tmp_path: Path, **extra) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", _NO_QT + code], cwd=str(REPO), env=_env(tmp_path, **extra),
                          capture_output=True, text=True, timeout=60)


def test_import_loads_no_qt(tmp_path):
    p = _run("import mangalist.headless, mangalist.headless.runner, mangalist.__main__\n"
             "assert not any(m.startswith('PySide6') for m in sys.modules)\n", tmp_path)
    assert p.returncode == 0, p.stderr


def test_headless_status_runs_without_qt(tmp_path):
    p = _run("from mangalist.__main__ import main\n"
             "rc = main(['--headless', '--status'])\n"
             "assert not any(m.startswith('PySide6') for m in sys.modules)\n"
             "sys.exit(rc)\n", tmp_path)
    assert p.returncode == 0, p.stderr
    assert "rescan" in p.stdout and "dispatch-batch" in p.stdout


def test_headless_run_rescan_once(tmp_path):
    lib = tmp_path / "lib" / "Series A"
    lib.mkdir(parents=True)
    (lib / "Series A v01.cbz").write_bytes(b"PK")
    p = _run("from mangalist.__main__ import main\n"
             "sys.exit(main(['--headless', '--run', 'rescan']))\n", tmp_path,
             MANGALIST_ROOTS=str(tmp_path / "lib"))
    assert p.returncode == 0, p.stderr
    state = json.loads((tmp_path / "data" / "headless-state.json").read_text(encoding="utf-8"))
    assert state["jobs"]["rescan"]["last_status"] == "ok"
    assert state["jobs"]["rescan"]["extra"]["roots"][0]["series"] == 1
    assert "1 series, 1 archives" in p.stdout
    assert (tmp_path / "data" / "logs" / "headless.log").is_file()
    assert not (tmp_path / "data" / "logs" / "mangalist.log").exists(), \
        "the runner never writes the GUI's log file"


def test_headless_unknown_job_and_bad_setting_fail_cleanly(tmp_path):
    p = _run("from mangalist.__main__ import main\n"
             "sys.exit(main(['--headless', '--run', 'nope']))\n", tmp_path)
    assert p.returncode == 2 and "unknown job" in p.stdout + p.stderr
    p = _run("from mangalist.__main__ import main\n"
             "sys.exit(main(['--headless']))\n", tmp_path,
             MANGALIST_RESCAN_SCHEDULE="sometimes")
    assert p.returncode == 2 and "unrecognised schedule" in p.stdout + p.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_sigterm_stops_the_runner_cleanly(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-c", _NO_QT + "from mangalist.__main__ import main\n"
                                        "sys.exit(main(['--headless']))\n"],
        cwd=str(REPO), env=_env(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True)
    try:
        state = tmp_path / "data" / "headless-state.json"
        deadline = time.monotonic() + 30
        while not state.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert state.exists(), "the runner planned its jobs"
        proc.send_signal(signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, out
    assert "Received SIGTERM" in out and "Scheduler stopped" in out


@pytest.mark.skipif(os.name == "nt", reason="POSIX file locks")
def test_second_runner_on_the_same_data_folder_refuses(tmp_path):
    from mangalist.headless.runner import InstanceLock

    lock = InstanceLock(tmp_path / "data" / "headless.lock")
    assert lock.acquire()
    try:
        p = _run("from mangalist.__main__ import main\n"
                 "sys.exit(main(['--headless', '--once']))\n", tmp_path)
        assert p.returncode == 3, p.stdout + p.stderr
    finally:
        lock.release()
