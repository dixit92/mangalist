"""Release versioning (packaging/stamp_version.py): calendar-version tags and the CHANGELOG check."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("stamp_version", ROOT / "packaging" / "stamp_version.py")
stamp_version = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stamp_version)

CHANGELOG = """# Changelog

## [Unreleased]

- next

## [2026.9.1] - 2026-09-30

- a fix

## [2026.9.0] - 2026-09-27

- first
"""


@pytest.mark.parametrize("version", ["2026.9.0", "2026.12.3", "2027.1.10"])
def test_calendar_versions_are_accepted(version):
    assert stamp_version.CALVER.match(version)


@pytest.mark.parametrize("version", ["2026.09.0", "2026.13.0", "2026.9", "1.2.3", "2026.9.01", "2026.9.0-rc1"])
def test_other_forms_are_rejected(version):
    assert not stamp_version.CALVER.match(version)


def test_changelog_section_is_the_body_of_that_version_only():
    assert stamp_version.changelog_section("2026.9.1", CHANGELOG) == "- a fix"
    assert stamp_version.changelog_section("2026.9.0", CHANGELOG) == "- first"
    assert stamp_version.changelog_section("2026.9.2", CHANGELOG) is None


def test_tag_build_needs_calver_and_a_changelog_section(monkeypatch, tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.setattr(stamp_version, "CHANGELOG", changelog)

    monkeypatch.setenv("GITHUB_REF", "refs/tags/v2026.9.1")
    assert stamp_version.compute() == ("2026.9.1", "2026.9.1")

    monkeypatch.setenv("GITHUB_REF", "refs/tags/v2026.10.0")
    with pytest.raises(SystemExit, match="no '## \\[2026.10.0\\]' section"):
        stamp_version.compute()

    monkeypatch.setenv("GITHUB_REF", "refs/tags/v1.2.3")
    with pytest.raises(SystemExit, match="not a release tag"):
        stamp_version.compute()


def test_other_builds_are_named_after_the_commit(monkeypatch):
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("STAMP_SHA", "abcdef1234567")
    assert stamp_version.compute() == ("0.0.0+abcdef1", "0.0.0")


def test_the_repository_changelog_has_an_unreleased_section():
    assert stamp_version.changelog_section("Unreleased") is not None
