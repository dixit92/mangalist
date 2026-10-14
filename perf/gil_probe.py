"""Second probe: widget counts and theme re-apply cost, and the GIL - how late the UI thread gets while a CPU-bound
background call (the Rename-pending count) runs. Research only."""

from __future__ import annotations

import json
import sys
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, "/tmp/n/perf")
from ui_profile import Heartbeat, pct, wait_until  # noqa: E402


def lag(g):
    return {"n": len(g), "p50": round(pct(g, 50) * 1000, 1), "p95": round(pct(g, 95) * 1000, 1),
            "p99": round(pct(g, 99) * 1000, 1), "max": round(max(g, default=0) * 1000, 1),
            "over50": sum(1 for x in g if x > 0.05), "over100": sum(1 for x in g if x > 0.1)}


def spin(app, s):
    end = time.perf_counter() + s
    while time.perf_counter() < end:
        app.processEvents()
        time.sleep(0.001)


def main():
    sys.argv = [a for a in sys.argv if not a.startswith("--out") and a != "/tmp/o"]
    from mangalist import log_config
    import mangalist.gui.main_window as mw
    from mangalist.gui.theme import apply_theme
    from mangalist.gui.background import start_call

    log_config.setup()
    app = QApplication(sys.argv)
    apply_theme(app)
    hb = Heartbeat()
    win = mw.MainWindow()
    win.show()
    app.processEvents()
    out = {"widgets_startup": len(app.allWidgets())}
    t = time.perf_counter()
    apply_theme(app)
    out["theme_reapply_startup_ms"] = round((time.perf_counter() - t) * 1000, 1)
    win.start_initial_scan()
    wait_until(app, lambda: win._thread is None, 600)
    wait_until(app, lambda: win._rename_call is None and win._dupe_call is None and win._sig_thread is None, 600)
    spin(app, 2)
    out["widgets_after_scan"] = len(app.allWidgets())
    types = {}
    for w in app.allWidgets():
        types[type(w).__name__] = types.get(type(w).__name__, 0) + 1
    out["widget_types_top"] = sorted(types.items(), key=lambda kv: -kv[1])[:15]
    out["hidden_widgets"] = sum(1 for w in app.allWidgets() if not w.isVisible())
    t = time.perf_counter()
    apply_theme(app)
    out["theme_reapply_after_scan_ms"] = round((time.perf_counter() - t) * 1000, 1)
    print(json.dumps(out), flush=True)

    # GIL: idle baseline, then the rename count in the background with the UI idle, then typing during it
    entries = win._model.entries()
    renamer = win._make_renamer()
    for interval in (0.005, 0.001):
        sys.setswitchinterval(interval)
        spin(app, 0.2)
        hb.take()
        spin(app, 3)
        idle = hb.take()
        done = {}
        t = time.perf_counter()
        start_call(lambda: renamer.pending_counts(entries), lambda r: done.setdefault("r", r),
                   lambda m: done.setdefault("e", m))
        # 4 s idle while it runs, then typing 6 keys while it runs
        spin(app, 4)
        busy_idle = hb.take()
        keys = []
        win._filter_edit.setFocus()
        for ch in "zqzq":
            k = time.perf_counter()
            QTest.keyClick(win._filter_edit, ch)
            keys.append(round((time.perf_counter() - k) * 1000, 1))
            spin(app, 0.3)
        hb.take()
        win._filter_edit.clear()
        wait_until(app, lambda: done, 300)
        total = time.perf_counter() - t
        # the same keys with nothing in the background
        spin(app, 1)
        keys_quiet = []
        for ch in "zqzq":
            k = time.perf_counter()
            QTest.keyClick(win._filter_edit, ch)
            keys_quiet.append(round((time.perf_counter() - k) * 1000, 1))
            spin(app, 0.3)
        win._filter_edit.clear()
        spin(app, 0.5)
        print(json.dumps({"switchinterval": interval, "rename_count_s": round(total, 1), "idle_lag": lag(idle),
                          "lag_with_bg_count": lag(busy_idle), "keys_during_bg_ms": keys,
                          "keys_quiet_ms": keys_quiet}), flush=True)
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
