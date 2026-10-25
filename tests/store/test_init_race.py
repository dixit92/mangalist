"""Two processes opening a fresh database at the same moment (the window and the headless runner of a container's
first start, owner 2026-10-24): exactly one migrates, the other waits and finds the schema current."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from contextlib import nullcontext

from mangalist import store
from mangalist.store import db as db_mod
from mangalist.store.schema import SCHEMA_VERSION


class _SlowVersionRead:
    """A connection whose ``PRAGMA user_version`` answer is delayed after it was read, so a second opener reads the
    same old version while the first is still about to migrate: the race made certain."""

    def __init__(self, con):
        self._con = con

    def execute(self, sql, *args):
        result = self._con.execute(sql, *args)
        if sql.startswith("PRAGMA user_version"):
            time.sleep(0.4)
        return result

    def __getattr__(self, name):
        return getattr(self._con, name)


def _race(path, monkeypatch, *, lock: bool, openers: int = 2):
    original = db_mod.StoreBase._migrate
    applied = []

    def slow_migrate(con):
        applied.append(threading.get_ident())
        return original(_SlowVersionRead(con))

    monkeypatch.setattr(db_mod.StoreBase, "_migrate", staticmethod(slow_migrate))
    if not lock:
        monkeypatch.setattr(db_mod, "init_file_lock", lambda *a, **k: nullcontext())
    errors, start = [], threading.Barrier(openers)

    def open_store():
        try:
            st = store.Store(path, import_legacy=False)        # each opener has its own Store: like two processes
            start.wait(5)
            st.ensure_initialized()
            assert st.schema_version() == SCHEMA_VERSION
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=open_store) for _ in range(openers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return errors


def test_the_race_is_real_without_the_lock(tmp_path, monkeypatch):
    errors = _race(tmp_path / "db" / "mangalist.db", monkeypatch, lock=False)
    assert errors, "two unlocked openers should collide (the lock test below would prove nothing otherwise)"
    assert any("duplicate column name" in str(e) or "already exists" in str(e) or "locked" in str(e) for e in errors)


def test_two_openers_of_a_fresh_database_migrate_it_once(tmp_path, monkeypatch):
    errors = _race(tmp_path / "db" / "mangalist.db", monkeypatch, lock=True, openers=3)
    assert errors == []


def test_two_processes_open_a_fresh_database_together(tmp_path):
    path = tmp_path / "fresh" / "mangalist.db"
    start_at = time.time() + 3.0
    code = ("import sys, time\nfrom pathlib import Path\nfrom mangalist import store\n"
            "time.sleep(max(0.0, float(sys.argv[2]) - time.time()))\n"
            "st = store.Store(Path(sys.argv[1]), import_legacy=False)\n"
            "st.ensure_initialized()\nprint(st.schema_version())\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [os.getcwd(), os.environ.get("PYTHONPATH", "")])))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(path), str(start_at)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, env=env) for _ in range(2)]
    results = [p.communicate(timeout=120) + (p.returncode,) for p in procs]
    for out, err, rc in results:
        assert rc == 0, err
        assert out.strip() == str(SCHEMA_VERSION)


def test_a_stale_lock_file_does_not_block(tmp_path):
    """The OS releases the lock when its holder dies; the file left behind is only a file."""
    path = tmp_path / "x" / "mangalist.db"
    path.parent.mkdir()
    (tmp_path / "x" / ("mangalist.db" + db_mod.INIT_LOCK_SUFFIX)).write_bytes(b"left by a crashed process")
    st = store.Store(path, import_legacy=False)
    st.ensure_initialized()
    assert st.schema_version() == SCHEMA_VERSION


def test_a_held_lock_makes_the_second_opener_wait_then_see_the_finished_schema(tmp_path):
    path = tmp_path / "y" / "mangalist.db"
    order = []
    with db_mod.init_file_lock(path):
        def other():
            st = store.Store(path, import_legacy=False)
            st.ensure_initialized()
            order.append("second opened")
        t = threading.Thread(target=other)
        t.start()
        time.sleep(0.3)
        assert order == []                      # still waiting for the lock
        order.append("first done")
    t.join(30)
    assert order == ["first done", "second opened"]
