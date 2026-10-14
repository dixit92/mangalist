"""UI-thread profile of MangaList against a real library (research only, never merged).

Runs the real MainWindow offscreen, starts the initial scan, waits for the background work, then drives the
interactions the owner uses and reports, per scenario: wall time, UI-thread busy time (the sum of top-level event
dispatches), the longest single block, and the event-loop lag seen by a 10 ms heartbeat. cProfile runs on the UI
thread only (it profiles the thread it is enabled in), so its numbers are exactly the work that blocks the window.

Usage (inside a container with PySide6 and the app on PYTHONPATH, QT_QPA_PLATFORM=offscreen):
    python perf/ui_profile.py [--out DIR] [--no-scan] [--theme dark]
"""

from __future__ import annotations

import argparse
import cProfile
import functools
import io
import json
import os
import pstats
import sys
import threading
import time
from collections import defaultdict

T0 = time.perf_counter()

from PySide6.QtCore import QEvent, QObject, QTimer, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    EVENT_NAMES = {int(m.value): n for n, m in QEvent.Type.__members__.items()}
except Exception:  # noqa: BLE001
    EVENT_NAMES = {}


class ProfApp(QApplication):
    """Times every top-level event dispatch on the UI thread (nested dispatches count once)."""

    def __init__(self, argv):
        super().__init__(argv)
        self.depth = 0
        self.busy = 0.0                 # seconds of top-level dispatch since the last reset
        self.longest = (0.0, "")
        self.blocks = []                # (start, duration, what) of dispatches >= 16 ms
        self.recording = True

    def notify(self, receiver, event):
        if self.depth or not self.recording:
            self.depth += 1
            try:
                return super().notify(receiver, event)
            finally:
                self.depth -= 1
        self.depth = 1
        t = time.perf_counter()
        try:
            return super().notify(receiver, event)
        finally:
            dt = time.perf_counter() - t
            self.depth = 0
            self.busy += dt
            if dt >= 0.016:
                try:
                    what = f"{EVENT_NAMES.get(int(event.type()), int(event.type()))} -> {type(receiver).__name__}"
                except Exception:  # noqa: BLE001 - the receiver may be gone
                    what = "?"
                self.blocks.append((t - T0, dt, what))
                if dt > self.longest[0]:
                    self.longest = (dt, what)

    def reset(self):
        self.busy, self.longest, self.blocks = 0.0, (0.0, ""), []


class Heartbeat(QObject):
    """A 10 ms timer: how late each tick is = how long the window could not react."""

    def __init__(self):
        super().__init__()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(10)
        self.timer.timeout.connect(self._tick)
        self.last = time.perf_counter()
        self.gaps = []
        self.timer.start()

    def _tick(self):
        now = time.perf_counter()
        self.gaps.append(now - self.last)
        self.last = now

    def take(self):
        g, self.gaps = self.gaps, []
        self.last = time.perf_counter()
        return g


CALLS = defaultdict(list)           # qualified name -> [seconds], UI thread only
_tl = threading.local()


def instrument(cls, names):
    for name in names:
        fn = cls.__dict__.get(name)
        if fn is None or not callable(fn):
            continue
        qual = f"{cls.__name__}.{name}"

        def wrap(fn=fn, qual=qual):
            @functools.wraps(fn)
            def inner(*a, **k):
                if threading.current_thread() is not threading.main_thread():
                    return fn(*a, **k)
                t = time.perf_counter()
                try:
                    return fn(*a, **k)
                finally:
                    CALLS[qual].append(time.perf_counter() - t)
            return inner

        setattr(cls, name, wrap())


def pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


class Session:
    def __init__(self, app, hb, out):
        self.app, self.hb, self.out = app, hb, out
        self.results = []
        self.prof_dir = os.path.join(out, "prof")
        os.makedirs(self.prof_dir, exist_ok=True)

    def settle(self, ms=400):
        """Run the event loop for *ms* (debounced timers, paints) and until no events are pending."""
        end = time.perf_counter() + ms / 1000
        while time.perf_counter() < end:
            self.app.processEvents()
            time.sleep(0.002)
        self.app.processEvents()

    def measure(self, name, action, settle_ms=400, profile=False, repeat_note=""):
        self.settle(50)
        self.app.reset()
        self.hb.take()
        calls_before = {k: len(v) for k, v in CALLS.items()}
        prof = cProfile.Profile() if profile else None
        t = time.perf_counter()
        if prof:
            prof.enable()
        direct_t = time.perf_counter()
        action()
        direct = time.perf_counter() - direct_t
        self.settle(settle_ms)
        if prof:
            prof.disable()
        wall = time.perf_counter() - t
        gaps = self.hb.take()
        calls = {k: round(sum(v[calls_before.get(k, 0):]) * 1000, 1) for k, v in CALLS.items()
                 if len(v) > calls_before.get(k, 0)}
        rec = {
            "scenario": name, "note": repeat_note,
            "direct_ms": round(direct * 1000, 1),          # the action itself (synchronous part)
            "busy_ms": round((self.app.busy + direct) * 1000, 1),   # + every event handled while settling
            "longest_block_ms": round(max(self.app.longest[0], direct) * 1000, 1),
            "longest_block": self.app.longest[1] if self.app.longest[0] > direct else "the action",
            "max_lag_ms": round(max(gaps, default=0) * 1000, 1),
            "wall_ms": round(wall * 1000, 1),
            "calls_ms": dict(sorted(calls.items(), key=lambda kv: -kv[1])[:12]),
        }
        self.results.append(rec)
        print(json.dumps(rec), flush=True)
        if prof:
            self.dump(prof, name)
        return rec

    def dump(self, prof, name):
        safe = "".join(c if c.isalnum() else "_" for c in name)[:60]
        prof.dump_stats(os.path.join(self.prof_dir, safe + ".prof"))
        s = io.StringIO()
        st = pstats.Stats(prof, stream=s)
        st.sort_stats("cumulative").print_stats(35)
        st.sort_stats("tottime").print_stats(25)
        with open(os.path.join(self.prof_dir, safe + ".txt"), "w") as fh:
            fh.write(s.getvalue())


def wait_until(app, cond, timeout, step=0.01):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(step)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/perf-out")
    ap.add_argument("--no-scan", action="store_true")
    ap.add_argument("--theme", default="")
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    marks = {}

    import mangalist.gui.main_window as mw
    from mangalist.gui import download_tab as dt_mod, detail_panel, table_model, list_tab
    from mangalist import log_config

    instrument(mw.MainWindow, [
        "_refresh_derived", "_update_counts", "_rebuild_wanted", "_update_detail_wanted", "_refresh_auto_modes",
        "_on_scan_finished", "_apply_root_scan", "_on_root_scanned", "_set_rows", "_prepare_entries",
        "_on_rename_counted", "_on_row_changed", "_on_filter_changed", "_on_library_changed", "_update_page",
        "_show_roots", "_update_missing_count", "_count_duplicate_files", "_on_duplicate_files_counted",
        "_count_rename_pending", "_start_signatures", "_on_signatures_finished", "_reload_examined",
        "_build_ui", "_restore_column_state", "_on_progress", "_mp_item_for", "_knowledge_for", "_inventory_for",
        "_update_detail_auto", "_refresh_libraries", "_apply_library", "start_initial_scan", "_start_scan",
    ])
    instrument(dt_mod.DownloadTab, ["set_wanted", "_rebuild", "reload_found", "_refresh_statuses", "_add_found_rows",
                                    "_on_current_changed", "_show_series", "show_group", "_on_records",
                                    "refresh", "_found_by_series", "found_names", "__init__", "_build_ui"])
    instrument(table_model.MangaTableModel, ["set_entries", "set_scope", "set_rename_counts", "scope_rows",
                                             "duplicate_series", "state_at", "data", "set_state_providers"])
    instrument(detail_panel.DetailPanel if hasattr(detail_panel, "DetailPanel") else type("X", (), {}),
               ["show_entry", "set_wanted"])
    instrument(list_tab.ListTab, ["set_counts", "set_filter", "__init__"])
    instrument(mw._SortProxy, ["filterAcceptsRow", "refresh_scope", "set_state_filter", "lessThan"])

    log_config.setup()
    marks["imports_s"] = time.perf_counter() - T0
    app = ProfApp(sys.argv)
    from mangalist.gui.theme import apply_theme
    t = time.perf_counter()
    apply_theme(app)
    from mangalist.gui import theme as theme_mod
    if args.theme and hasattr(theme_mod, "set_appearance"):
        theme_mod.set_appearance(app, args.theme)
    marks["apply_theme_s"] = time.perf_counter() - t
    hb = Heartbeat()
    sess = Session(app, hb, args.out)

    prof = cProfile.Profile()
    t = time.perf_counter()
    prof.enable()
    win = mw.MainWindow()
    marks["mainwindow_ctor_s"] = time.perf_counter() - t
    win.show()
    app.processEvents()
    prof.disable()
    marks["first_show_s"] = time.perf_counter() - t
    marks["process_to_window_s"] = time.perf_counter() - T0
    sess.dump(prof, "startup")
    print("MARKS", json.dumps(marks), flush=True)

    # --- the initial scan: what the UI thread does while the scanner, then the rename count, run
    if not args.no_scan:
        app.reset()
        hb.take()
        prof = cProfile.Profile()
        prof.enable()
        t = time.perf_counter()
        win.start_initial_scan()
        wait_until(app, lambda: win._thread is None, 600)
        scan_s = time.perf_counter() - t
        gaps_scan = hb.take()
        busy_scan, blocks_scan = app.busy, list(app.blocks)
        app.reset()
        t2 = time.perf_counter()
        # post-scan background work: rename-pending count, duplicates count, signatures
        wait_until(app, lambda: win._rename_call is None and win._dupe_call is None and win._sig_thread is None, 600)
        post_s = time.perf_counter() - t2
        prof.disable()
        gaps_post = hb.take()
        sess.dump(prof, "scan_and_post")

        def lagstats(g):
            return {"n": len(g), "p50_ms": round(pct(g, 50) * 1000, 1), "p95_ms": round(pct(g, 95) * 1000, 1),
                    "p99_ms": round(pct(g, 99) * 1000, 1), "max_ms": round(max(g, default=0) * 1000, 1),
                    "over_100ms": sum(1 for x in g if x > 0.1), "over_50ms": sum(1 for x in g if x > 0.05)}
        scan = {"scan_wall_s": round(scan_s, 1), "scan_ui_busy_s": round(busy_scan, 2),
                "scan_lag": lagstats(gaps_scan), "scan_blocks_top": sorted(blocks_scan, key=lambda b: -b[1])[:15],
                "post_wall_s": round(post_s, 1), "post_ui_busy_s": round(app.busy, 2), "post_lag": lagstats(gaps_post),
                "post_blocks_top": sorted(app.blocks, key=lambda b: -b[1])[:15],
                "rows": win._model.rowCount()}
        print("SCAN", json.dumps(scan), flush=True)
        sess.results.append({"scenario": "scan", **scan})
        idle = []
        sess.settle(3000)
        idle = hb.take()
        print("IDLE", json.dumps({"p50_ms": round(pct(idle, 50) * 1000, 1), "max_ms": round(max(idle) * 1000, 1)}))

    # --- interactions
    from mangalist.gui.list_tab import all_filter_keys
    from mangalist.gui.top_bar import TAB_DOWNLOAD, TAB_LIST

    sess.measure("refresh_derived (direct)", win._refresh_derived, profile=True)
    sess.measure("refresh_derived (direct, again)", win._refresh_derived)
    sess.measure("rebuild_wanted force", lambda: win._rebuild_wanted(force=True), profile=True)
    for i in range(3):
        sess.measure("tab -> Download", lambda: win.show_tab(TAB_DOWNLOAD), profile=(i == 0), repeat_note=str(i))
        sess.measure("tab -> List", lambda: win.show_tab(TAB_LIST), repeat_note=str(i))
    for key in all_filter_keys():
        sess.measure(f"filter chip {key}", lambda key=key: win._list.set_filter(key, emit=True),
                     profile=(key == "behind"))
    win._list.set_filter(None, emit=True)
    sess.settle(500)
    # typing in the search box, one key at a time (made-up text)
    win._filter_edit.setFocus()
    for i, ch in enumerate("the"):
        sess.measure(f"type '{ch}'", lambda ch=ch: QTest.keyClick(win._filter_edit, ch), settle_ms=60,
                     profile=(i == 0))
    for i in range(3):
        sess.measure("backspace", lambda: QTest.keyClick(win._filter_edit, Qt.Key.Key_Backspace), settle_ms=60)
    for i, ch in enumerate("zq"):
        sess.measure(f"type '{ch}' (no match)", lambda ch=ch: QTest.keyClick(win._filter_edit, ch), settle_ms=60)
    win._filter_edit.clear()
    sess.settle(300)
    # sorting
    header = win._table.horizontalHeader()
    for col in range(win._model.columnCount()):
        if header.isSectionHidden(col):
            continue
        name = win._model.headerData(col, Qt.Orientation.Horizontal)
        sess.measure(f"sort {name}", lambda col=col: win._table.sortByColumn(col, Qt.SortOrder.DescendingOrder),
                     profile=(col == 2))
    win._table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
    # library picker
    roots = [r.id for r in win._roots()]
    for i, rid in enumerate(roots + [None]):
        sess.measure(f"library {'all' if rid is None else 'root#' + str(i)}",
                     lambda rid=rid: win._top.set_library(rid, emit=True), profile=(i == 0))
    # selecting rows (the details panel)
    n = win._proxy.rowCount()
    for i, r in enumerate(range(0, n, max(1, n // 25))):
        sess.measure("select row", lambda r=r: win._table.selectRow(r), settle_ms=80, profile=(i == 1),
                     repeat_note=str(r))
    # scrolling the table a page at a time
    sb = win._table.verticalScrollBar()
    for i in range(8):
        sess.measure("scroll page", lambda: sb.setValue(sb.value() + sb.pageStep()), settle_ms=40)
    # details panel toggle, window resize
    sess.measure("details hide", lambda: win._list.set_details_visible(False, emit=True))
    sess.measure("details show", lambda: win._list.set_details_visible(True, emit=True))
    sess.measure("resize 1920x1080", lambda: win.resize(1920, 1080), profile=True)
    sess.measure("resize 1440x900", lambda: win.resize(1440, 900))
    # the Download tab
    tab = win._download_tab
    if tab is not None:
        win.show_tab(TAB_DOWNLOAD)
        sess.settle(300)
        sess.measure("download set_wanted (same list, forced)", lambda: tab.set_wanted(list(win._wanted_series)),
                     profile=True)
        sess.measure("download _rebuild", tab._rebuild, profile=True)
        sess.measure("download reload_found", tab.reload_found, settle_ms=2000, profile=True)
        for g in tab.chip_groups() if callable(getattr(tab, "chip_groups", None)) else ():
            key = g[0] if isinstance(g, tuple) else g
            sess.measure(f"download group {key}", lambda key=key: tab.show_group(key), profile=False)
        tree = getattr(tab, "_tree", None) or getattr(tab, "tree", None)
        if tree is not None and tree.topLevelItemCount():
            for i in range(min(10, tree.topLevelItemCount())):
                it = tree.topLevelItem(i)
                sess.measure("download select series", lambda it=it: tree.setCurrentItem(it), settle_ms=300,
                             profile=(i == 1))
        sess.results.append({"scenario": "download counts", "wanted": len(win._wanted_series),
                             "findings": len(tab.findings()) if callable(getattr(tab, "findings", None)) else None,
                             "tree_items": tree.topLevelItemCount() if tree is not None else None})
        win.show_tab(TAB_LIST)
        sess.settle(300)
    # Settings and the Renamer
    from mangalist.gui.settings_dialog import SettingsDialog
    holder = {}

    def open_settings():
        holder["dlg"] = SettingsDialog(win, win._db, win._volumes_backend, None)
        holder["dlg"].show()
    sess.measure("open Settings", open_settings, settle_ms=1500, profile=True)
    sess.measure("close Settings", lambda: holder["dlg"].reject())
    from mangalist.gui.renamer_view import Scope
    sess.measure("open Renamer (every library)", lambda: holder.__setitem__("ren", win.open_renamer(Scope.all())),
                 settle_ms=500, profile=True)
    t = time.perf_counter()
    wait_until(app, lambda: not getattr(holder["ren"], "busy", False), 300)
    sess.results.append({"scenario": "Renamer dry run (every library) background", "wall_s":
                         round(time.perf_counter() - t, 1)})
    sess.measure("Renamer after dry run (paint)", lambda: None, settle_ms=500)
    sess.measure("close Renamer", lambda: holder["ren"].close())
    # duplicates chip
    sess.measure("filter chip duplicates (again)", lambda: win._list.set_filter(list_tab.DUPLICATES, emit=True),
                 settle_ms=1500, profile=True)
    sess.measure("filter chip all (back)", lambda: win._list.set_filter(None, emit=True))
    print("WIDGETS", len(app.allWidgets()), flush=True)
    if hasattr(theme_mod, "set_appearance"):
        other = "light" if args.theme == "dark" else "dark"
        sess.measure(f"theme switch -> {other}", lambda: theme_mod.set_appearance(app, other), profile=True)
        sess.measure(f"theme switch -> {args.theme or 'light'}",
                     lambda: theme_mod.set_appearance(app, args.theme or "light"))
    else:
        sess.measure("theme re-apply", lambda: apply_theme(app), profile=True)

    summary = {"marks": marks, "results": sess.results,
               "calls": {k: {"n": len(v), "total_ms": round(sum(v) * 1000, 1), "max_ms": round(max(v) * 1000, 1)}
                         for k, v in sorted(CALLS.items(), key=lambda kv: -sum(kv[1]))}}
    with open(os.path.join(args.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    print("CALLS", json.dumps(summary["calls"]), flush=True)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
