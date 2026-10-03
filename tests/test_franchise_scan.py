"""Tests for franchise subdirectory scanning."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mangalist.scanner import (
    _extract_subseries,
    _get_subdirs_with_archives,
    _has_direct_archives,
    scan_root,
)


def _create_fake_archive(folder: Path, name: str) -> Path:
    """Create a fake archive file for testing."""
    archive = folder / name
    archive.write_bytes(b"PK\x03\x04fake_zip_content")
    return archive


class TestFranchiseDetection:
    """Test detection of parent franchise folders."""

    def test_has_direct_archives_with_files(self, tmp_path: Path):
        """Folder with direct archives returns True."""
        _create_fake_archive(tmp_path, "Vol.1.cbz")
        assert _has_direct_archives(tmp_path) is True

    def test_has_direct_archives_empty(self, tmp_path: Path):
        """Empty folder returns False."""
        assert _has_direct_archives(tmp_path) is False

    def test_has_direct_archives_only_subdirs(self, tmp_path: Path):
        """Folder with only subdirs (no direct files) returns False."""
        (tmp_path / "subdir").mkdir()
        assert _has_direct_archives(tmp_path) is False

    def test_get_subdirs_with_archives(self, tmp_path: Path):
        """Finds subdirectories containing archives."""
        sub1 = tmp_path / "Series A"
        sub1.mkdir()
        _create_fake_archive(sub1, "Vol.1.cbz")

        sub2 = tmp_path / "Series B"
        sub2.mkdir()
        _create_fake_archive(sub2, "Ch.1.cbz")

        # Empty subdir
        (tmp_path / "Empty").mkdir()

        result = _get_subdirs_with_archives(tmp_path)
        assert len(result) == 2
        assert sub1 in result
        assert sub2 in result


class TestExtractSubseries:
    """Test extraction of subseries entries from franchise parent."""

    def test_extract_subseries_basic(self, tmp_path: Path):
        """Extract entries for each subseries."""
        parent = tmp_path / "Fate"
        parent.mkdir()

        sub1 = parent / "stay night"
        sub1.mkdir()
        _create_fake_archive(sub1, "Vol.1.cbz")

        sub2 = parent / "Zero"
        sub2.mkdir()
        _create_fake_archive(sub2, "Vol.1.cbz")

        entries = _extract_subseries(parent, "Fate", 0.0)

        assert len(entries) == 2
        assert all(e.parent_folder == parent for e in entries)
        assert any(e.title == "stay night" for e in entries)
        assert any(e.title == "Zero" for e in entries)

    def test_skip_chapters_subdir(self, tmp_path: Path):
        """Skip subdirectories named 'Chapters'."""
        parent = tmp_path / "Manga"
        parent.mkdir()

        sub1 = parent / "Chapters"  # Should be skipped
        sub1.mkdir()
        _create_fake_archive(sub1, "Ch.10.cbz")

        sub2 = parent / "Volume 1"  # Should be included
        sub2.mkdir()
        _create_fake_archive(sub2, "Vol.1.cbz")

        entries = _extract_subseries(parent, "Manga", 0.0)

        assert len(entries) == 1
        assert entries[0].title == "Volume 1"

    def test_skip_extras_subdir(self, tmp_path: Path):
        """Skip subdirectories named 'Extras', 'Bonus', 'Specials', 'Omake'."""
        parent = tmp_path / "Manga"
        parent.mkdir()

        for skip_name in ["extras", "bonus", "specials", "omake"]:
            sub = parent / skip_name
            sub.mkdir()
            _create_fake_archive(sub, "extra.cbz")

        real = parent / "Main Series"
        real.mkdir()
        _create_fake_archive(real, "Vol.1.cbz")

        entries = _extract_subseries(parent, "Manga", 0.0)

        assert len(entries) == 1
        assert entries[0].title == "Main Series"


class TestScanRootFranchise:
    """Integration tests for scan_root with franchise folders."""

    def test_franchise_parent_hidden(self, tmp_path: Path):
        """Parent with no direct files but subseries - parent hidden, subs shown."""
        root = tmp_path / "root"
        root.mkdir()

        franchise = root / "Fate"
        franchise.mkdir()

        sub = franchise / "stay night"
        sub.mkdir()
        _create_fake_archive(sub, "Vol.1.cbz")

        entries = scan_root(root)

        # Should only have the subseries, not the parent
        assert len(entries) == 1
        assert entries[0].title == "stay night"
        assert entries[0].parent_folder == franchise

    def test_parent_with_files_shown(self, tmp_path: Path):
        """Parent with direct files AND subseries - both shown."""
        root = tmp_path / "root"
        root.mkdir()

        franchise = root / "Big Series"
        franchise.mkdir()

        # Direct file in parent
        _create_fake_archive(franchise, "Vol.1.cbz")

        # Subseries
        sub = franchise / "Side Story"
        sub.mkdir()
        _create_fake_archive(sub, "Vol.1.cbz")

        entries = scan_root(root)

        # Should have both parent and subseries
        assert len(entries) == 2
        titles = {e.title for e in entries}
        assert "Big Series" in titles
        assert "Side Story" in titles

        # Subseries should have parent marked
        sub_entry = next(e for e in entries if e.title == "Side Story")
        assert sub_entry.parent_folder == franchise

    def test_normal_folder_unchanged(self, tmp_path: Path):
        """Normal folders without subseries work as before."""
        root = tmp_path / "root"
        root.mkdir()

        manga = root / "Solo Manga"
        manga.mkdir()
        _create_fake_archive(manga, "Vol.1.cbz")

        entries = scan_root(root)

        assert len(entries) == 1
        assert entries[0].title == "Solo Manga"
        assert entries[0].parent_folder is None

    def test_title_format_includes_parent(self, tmp_path: Path):
        """Subseries title display includes parent name."""
        root = tmp_path / "root"
        root.mkdir()

        franchise = root / "Fate"
        franchise.mkdir()

        sub = franchise / "Zero"
        sub.mkdir()
        _create_fake_archive(sub, "Vol.1.cbz")

        entries = scan_root(root)

        assert len(entries) == 1
        # The entry should have parent_folder set
        assert entries[0].parent_folder.name == "Fate"
        assert entries[0].title == "Zero"
