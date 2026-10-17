"""Findings whose gaps are filled leave Found after a rescan (the container's) and through the adapter (the window's),
not only after a filing or the next find pass (owner, 2026-10-16). Made-up data."""

from __future__ import annotations

from mangalist.downloads import adapter as adapter_mod
from mangalist.downloads import finder


def test_the_adapter_retires_with_its_own_wanted_list(monkeypatch):
    seen = []
    monkeypatch.setattr(finder, "retire_filled", lambda db, wanted=None: seen.append((db, wanted)) or 2)
    fake = type("A", (), {"db": "DB", "_wanted": ["w"]})()
    assert adapter_mod.Backend.retire_filled(fake) == 2 and seen == [("DB", ["w"])]
