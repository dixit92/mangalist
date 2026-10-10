"""The renamer's automatic pass in the headless rescan (a real database over a made-up library in a temporary folder,
the FAKE namer; no MangaPixer)."""

from __future__ import annotations

from pathlib import Path

from mangalist.headless.jobs import Cancelled, JobContext, StoreRootsProvider, make_rescan
from mangalist.renamer import Renamer

from .conftest import fmd2, make_library, payload
from .fakes import FakeNamer, Recorder


def _rescan(db, renames):
    return make_rescan(StoreRootsProvider(env={}, db=db), backfill=False, renames=renames)(JobContext())


def test_the_rescan_renames_roots_set_to_automatic(db, library):
    root = make_library(db, library, {"Series H": {fmd2(1, "0001", "One", "G"): payload("h1")}})
    hooks = Recorder()
    passes = []

    def renames(db_, ctx):
        r = Renamer(db_, namer=FakeNamer(), title_for=lambda *a: None, rescan=hooks.rescan,
                    request_scans=hooks.request_scans)
        report = r.automatic_pass(should_stop=lambda: ctx.stop_requested)
        passes.append(report)
        return report.summary()

    res = _rescan(db, renames)
    assert res.status == "ok" and res.extra["renames"] == "renames: no library renames automatically"
    assert res.message.endswith("; renames: no library renames automatically")
    root.enforce_naming = "automatic"
    db.update_root(root)
    Renamer(db, namer=FakeNamer()).mark_guided([root.id])
    res = _rescan(db, renames)
    assert "1 file(s) renamed in 1 batch(es)" in res.extra["renames"]
    assert [p.name for p in (Path(library) / "Series H").iterdir()] == ["Ch. 0001.00 (One) [G].cbz"]
    assert hooks.rescans == [[root.id]] and len(hooks.scan_requests) == 1


def test_the_default_pass_without_automatic_roots_needs_no_naming_module(db, library):
    make_library(db, library, {"Series H": {fmd2(1, "0001"): payload("h1")}})
    res = make_rescan(StoreRootsProvider(env={}, db=db), backfill=False)(JobContext())
    assert res.extra["renames"] == "renames: no library renames automatically"


def test_a_failing_pass_does_not_fail_the_rescan(db, library):
    make_library(db, library, {"Series H": {fmd2(1, "0001"): payload("h1")}})

    def broken(db_, ctx):
        raise RuntimeError("boom")

    res = _rescan(db, broken)
    assert res.status == "ok" and res.extra["renames"] == "renames: failed (RuntimeError)"


def test_a_shutdown_stops_before_the_pass(db, library):
    make_library(db, library, {"Series H": {fmd2(1, "0001"): payload("h1")}})
    asked = []
    stop = {"now": False}

    def renames(db_, ctx):
        asked.append(1)
        return "x"

    ctx = JobContext(lambda: stop["now"])
    job = make_rescan(StoreRootsProvider(env={}, db=db), backfill=False, renames=renames)
    assert job(ctx).extra["renames"] == "x"
    stop["now"] = True
    try:
        job(ctx)
    except Cancelled:
        pass
    assert asked == [1]
