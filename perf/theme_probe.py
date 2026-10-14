"""Theme cost with an honest widget count: deferred deletes are flushed first (the profile harness never enters
exec(), so rebuilt rows piled up there). Times re-applying the theme (release) or switching Light/Dark (dark-mode
branch) at start-up and after the scan, with and without the Found cards. Research only."""

from __future__ import annotations

import cProfile
import io
import json
import pstats
import sys
import time

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

sys.path.insert(0, "/tmp/n/perf")
from ui_profile import wait_until  # noqa: E402


def flush(app):
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def main():
    sys.argv = [a for a in sys.argv if not a.startswith("--out") and a != "/tmp/o"]
    from mangalist import log_config
    import mangalist.gui.main_window as mw
    from mangalist.gui import theme

    log_config.setup()
    app = QApplication(sys.argv)
    theme.apply_theme(app)
    switch = hasattr(theme, "set_appearance")

    def retheme(choice):
        if switch:
            theme.set_appearance(app, choice)
        else:
            theme.apply_theme(app)

    def timed(label, choice, prof=False):
        flush(app)
        n = len(app.allWidgets())
        p = cProfile.Profile() if prof else None
        t = time.perf_counter()
        if p:
            p.enable()
        retheme(choice)
        app.processEvents()
        if p:
            p.disable()
        ms = round((time.perf_counter() - t) * 1000, 1)
        out = {"what": label, "widgets": n, "ms": ms}
        if p:
            s = io.StringIO()
            pstats.Stats(p, stream=s).sort_stats("cumulative").print_stats(14)
            out["top"] = [ln.strip()[:150] for ln in s.getvalue().splitlines() if "{" in ln or ".py" in ln][:14]
        print(json.dumps(out), flush=True)

    win = mw.MainWindow()
    win.show()
    app.processEvents()
    timed("startup, dark" if switch else "startup, re-apply", "dark", prof=True)
    timed("startup, light" if switch else "startup, re-apply 2", "light")
    win.start_initial_scan()
    wait_until(app, lambda: win._thread is None, 600)
    wait_until(app, lambda: win._rename_call is None and win._dupe_call is None and win._sig_thread is None, 600)
    timed("after scan, dark" if switch else "after scan, re-apply", "dark")
    timed("after scan, light" if switch else "after scan, re-apply 2", "light")
    tab = win._download_tab
    if tab is not None:
        tab.found.set_findings([], {}) if hasattr(tab, "found") else None
        flush(app)
        timed("after scan, Found emptied, dark" if switch else "after scan, Found emptied, re-apply", "dark",
              prof=True)
        timed("after scan, Found emptied, light" if switch else "after scan, Found emptied, re-apply 2", "light")
    win.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
