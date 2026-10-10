"""The styled spin boxes show their up / down arrows (owner, 2026-10-10: "Arrow buttons don't show")."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QSpinBox, QWidget  # noqa: E402

from mangalist.gui import spin_arrows  # noqa: E402
from mangalist.gui.download_style import apply_style  # noqa: E402

from .conftest import qapp  # noqa: E402,F401


def test_the_arrows_are_drawn_once_and_the_rules_use_them(qapp):
    files = spin_arrows.arrow_files()
    assert files and set(files) == {"up", "down", "up_off", "down_off"}
    assert all(os.path.isfile(p) and "\\" not in p for p in files.values())
    assert spin_arrows.arrow_files() is files                           # once per process
    rules = spin_arrows.spin_rules()
    assert "QSpinBox::up-arrow" in rules and files["up"] in rules and "padding-right: 26px" in rules


def test_a_spin_box_in_the_settings_sheet_shows_dark_arrow_pixels(qapp):
    host = QWidget()
    apply_style(host)
    box = QSpinBox(host)
    box.setRange(0, 100)
    box.setValue(50)
    box.resize(140, 34)
    host.resize(160, 50)
    host.show()
    qapp.processEvents()
    img = box.grab().toImage()
    w, h = img.width(), img.height()
    dark = sum(1 for x in range(w - 22, w - 2) for y in range(2, h - 2)
               if QColor(img.pixel(x, y)).lightness() < 140)
    host.close()
    assert dark > 6, "no arrow drawn in the button area"


def test_without_the_arrow_files_no_rules_are_added(qapp, monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")                                             # a folder cannot be made under a file, on any OS
    monkeypatch.setattr(spin_arrows, "_made", {})
    monkeypatch.setattr(spin_arrows, "_cache_dir", lambda: str(blocker / "ui"))
    assert spin_arrows.spin_rules(color="#123456") == ""


def test_the_value_has_room_before_it(qapp):
    from PySide6.QtWidgets import QLineEdit

    box = spin_arrows.pad_spin(QSpinBox())
    assert box.findChild(QLineEdit).textMargins().left() == 8
