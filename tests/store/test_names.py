"""Windows-safe names."""

from __future__ import annotations

import pytest

from mangalist.store import windows_name_problem, windows_safe_name


@pytest.mark.parametrize("name", ["Series A v01 (2020) (Digital) (Group).cbz", "Ch. 0002.1.cbz", "[Group] Title",
                                  "CONsole.cbz", "a.b.c", "Title No. 5"])
def test_valid(name):
    assert windows_name_problem(name) is None
    assert windows_safe_name(name) == name


@pytest.mark.parametrize("name, safe", [
    ("Title: Subtitle", "Title_ Subtitle"),
    ('Who? "Me"*', "Who_ _Me__"),
    ("a/b\\c|d<e>f", "a_b_c_d_e_f"),
    ("Ends with dot.", "Ends with dot"),
    ("Ends with space ", "Ends with space"),
    ("CON", "CON_"),
    ("nul.cbz", "nul_.cbz"),
    ("LPT1 .txt", "LPT1 _.txt"),
    ("tab\there", "tab_here"),
    ("...", "_"),
])
def test_invalid_and_made_safe(name, safe):
    assert windows_name_problem(name)
    assert windows_safe_name(name) == safe
    assert windows_name_problem(windows_safe_name(name)) is None


def test_long_names_keep_their_extension():
    long = "x" * 300 + ".cbz"
    assert "longer than" in windows_name_problem(long)
    safe = windows_safe_name(long)
    assert len(safe) == 255 and safe.endswith(".cbz")


def test_unsafe_replacement_is_refused():
    with pytest.raises(ValueError):
        windows_safe_name("a:b", replacement=":")
