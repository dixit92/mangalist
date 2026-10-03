"""``python -m mangalist`` options used by packaging checks and CI."""

from __future__ import annotations

from mangalist import __version__
from mangalist.__main__ import main


def test_version_prints_and_exits_without_a_window(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"MangaList {__version__}"
