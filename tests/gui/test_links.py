"""Opening a folder: the program MANGALIST_FILE_MANAGER names (the container), else the platform's own way. Made-up
paths only; no file manager is started."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from mangalist.gui import links  # noqa: E402

FOLDER = "/data/Library/Example Series"


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(links.QProcess, "startDetached",
                        staticmethod(lambda program, args: calls.append((program, args)) or (True, 4242)))
    return calls


def test_the_named_file_manager_gets_the_folder(qapp, monkeypatch, started):
    monkeypatch.setenv(links.FILE_MANAGER_ENV, "pcmanfm")
    assert links.open_folder(FOLDER) is True
    assert started == [("pcmanfm", [FOLDER])]


def test_the_named_file_manager_may_carry_arguments(qapp, monkeypatch, started):
    monkeypatch.setenv(links.FILE_MANAGER_ENV, "xfe --no-tree")
    assert links.open_folder(FOLDER) is True
    assert started == [("xfe", ["--no-tree", FOLDER])]


def test_a_file_manager_that_does_not_start_is_false(qapp, monkeypatch):
    monkeypatch.setenv(links.FILE_MANAGER_ENV, "no-such-program")
    monkeypatch.setattr(links.QProcess, "startDetached", staticmethod(lambda program, args: (False, 0)))
    assert links.open_folder(FOLDER) is False


def test_without_one_named_the_desktop_opens_it(qapp, monkeypatch, started):
    monkeypatch.delenv(links.FILE_MANAGER_ENV, raising=False)
    monkeypatch.setattr(links.sys, "platform", "linux")
    asked = []
    monkeypatch.setattr(links.QDesktopServices, "openUrl", staticmethod(lambda url: asked.append(url) or False))
    assert links.open_folder(FOLDER) is False
    assert [u.toLocalFile() for u in asked] == [FOLDER] and started == []
