"""``.mangalist.lock``: one MangaList writer per root; plain-file based; stale takeover."""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from mangalist.store import LOCK_NAME, LockBusy, LockError, LockLost, RootLock


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_acquire_writes_a_plain_json_file_and_release_removes_it(library):
    lock = RootLock(library, host="host-a")
    lock.acquire()
    data = json.loads((library / LOCK_NAME).read_text(encoding="utf-8"))
    assert data["host"] == "host-a" and data["pid"] == os.getpid() and data["app"] == "MangaList"
    assert lock.held and lock.read().token == lock.token
    lock.release()
    assert not (library / LOCK_NAME).exists() and not lock.held


def test_a_second_instance_is_refused_while_the_first_is_alive(library):
    clock = Clock()
    first = RootLock(library, host="unraid", clock=clock)
    first.acquire()
    second = RootLock(library, host="windows-pc", clock=clock)
    with pytest.raises(LockBusy) as err:
        second.acquire()
    assert err.value.holder.host == "unraid"
    clock.t += 200                                   # within the stale window
    with pytest.raises(LockBusy):
        second.acquire()
    first.refresh()                                  # heartbeat
    clock.t += 200
    with pytest.raises(LockBusy):
        second.acquire()


def test_a_stale_heartbeat_is_taken_over(library):
    clock = Clock()
    first = RootLock(library, host="unraid", clock=clock, stale_after=300)
    first.acquire()
    clock.t += 301
    second = RootLock(library, host="windows-pc", clock=clock, stale_after=300)
    second.acquire()
    assert second.read().token == second.token
    with pytest.raises(LockLost):
        first.refresh()
    assert first.lost
    first.release()                                  # not ours any more: left alone
    assert second.read().token == second.token
    assert sorted(p.name for p in library.iterdir()) == [LOCK_NAME]  # no takeover leftovers


def test_a_dead_process_on_the_same_host_is_taken_over_at_once(library):
    clock = Clock()
    dead = RootLock(library, host="same-box", pid=2 ** 22 + 12345, clock=clock)  # no such pid
    dead.acquire()
    other = RootLock(library, host="same-box", clock=clock)
    other.acquire()
    assert other.held


def test_a_live_process_on_the_same_host_is_respected(library, live_pid):
    clock = Clock()
    alive = RootLock(library, host="same-box", pid=live_pid, clock=clock)
    alive.acquire()
    with pytest.raises(LockBusy):
        RootLock(library, host="same-box", clock=clock).acquire()


def test_force_takes_over_a_live_lock(library):
    first = RootLock(library, host="a")
    first.acquire()
    second = RootLock(library, host="b")
    second.acquire(force=True)
    with pytest.raises(LockLost):
        first.check()


def test_an_unreadable_lock_file_is_stale_only_once_old(library):
    clock = Clock()
    (library / LOCK_NAME).write_text("{ half written", encoding="utf-8")
    mtime = (library / LOCK_NAME).stat().st_mtime
    clock.t = mtime + 10
    with pytest.raises(LockBusy):
        RootLock(library, clock=clock).acquire()
    clock.t = mtime + 1000
    lock = RootLock(library, clock=clock)
    lock.acquire()
    assert lock.held


def test_the_takeover_gives_up_when_the_lock_changed_in_between(library):
    clock = Clock()
    holder = RootLock(library, host="a", clock=clock)
    holder.acquire()
    taker = RootLock(library, host="b", clock=clock)
    judged = taker.read()
    clock.t += 1
    holder.refresh()                                # the holder came back between "judge" and "take"
    with pytest.raises(LockBusy):
        taker._take_over(judged)
    assert holder.read().token == holder.token     # put back
    holder.check()


def test_context_manager_and_heartbeat_thread(library):
    with RootLock(library, heartbeat_every=0.05) as lock:
        first = lock.read().heartbeat
        import time
        deadline = time.time() + 5
        while lock.read().heartbeat == first and time.time() < deadline:
            time.sleep(0.02)
        assert lock.read().heartbeat > first
    assert not (library / LOCK_NAME).exists()


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs a POSIX permission check (root ignores it)")
def test_a_read_only_root_cannot_be_locked_but_can_be_read(library):
    library.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(LockError):
            RootLock(library).acquire()
        assert RootLock(library).read() is None
    finally:
        library.chmod(stat.S_IRWXU)


def test_files_written_by_other_tools_do_not_matter(library):
    with RootLock(library):
        (library / "Series A").mkdir()
        (library / "Series A" / "new chapter.cbz").write_bytes(b"x")  # e.g. FFS adding a file
    assert (library / "Series A" / "new chapter.cbz").is_file()
