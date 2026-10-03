"""Tests for duplicate MU title detection."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from mangalist.models import MangaEntry
from mangalist.gui.table_model import MangaTableModel


def test_duplicate_detection_basic():
    """Test basic duplicate detection based on MU title."""
    # Create entries with same MU title (different folders)
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = "Same Series"
    entry1.mu_id = 123

    entry2 = MangaEntry(
        folder=Path("/manga/Series B"),
        title="Series B",
        english_title=None,
    )
    entry2.mu_title = "Same Series"  # Same MU title
    entry2.mu_id = 123

    entry3 = MangaEntry(
        folder=Path("/manga/Series C"),
        title="Series C",
        english_title=None,
    )
    entry3.mu_title = "Different Series"  # Different MU title
    entry3.mu_id = 456

    model = MangaTableModel([entry1, entry2, entry3])

    # entry1 and entry2 are duplicates
    assert model.is_duplicate(0) is True
    assert model.is_duplicate(1) is True

    # entry3 is not a duplicate
    assert model.is_duplicate(2) is False


def test_duplicate_detection_case_insensitive():
    """Duplicate detection should be case-insensitive."""
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = "SAME SERIES"

    entry2 = MangaEntry(
        folder=Path("/manga/Series B"),
        title="Series B",
        english_title=None,
    )
    entry2.mu_title = "same series"  # Different case

    model = MangaTableModel([entry1, entry2])

    assert model.is_duplicate(0) is True
    assert model.is_duplicate(1) is True


def test_duplicate_detection_whitespace():
    """Duplicate detection should handle whitespace differences."""
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = "  Same Series  "  # Extra whitespace

    entry2 = MangaEntry(
        folder=Path("/manga/Series B"),
        title="Series B",
        english_title=None,
    )
    entry2.mu_title = "Same Series"

    model = MangaTableModel([entry1, entry2])

    assert model.is_duplicate(0) is True
    assert model.is_duplicate(1) is True


def test_no_mu_title_not_duplicate():
    """Entries without MU titles are never duplicates."""
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = None

    entry2 = MangaEntry(
        folder=Path("/manga/Series B"),
        title="Series B",
        english_title=None,
    )
    entry2.mu_title = None

    model = MangaTableModel([entry1, entry2])

    assert model.is_duplicate(0) is False
    assert model.is_duplicate(1) is False


def test_get_duplicate_rows():
    """Test getting list of duplicate row indices."""
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = "Same Series"

    entry2 = MangaEntry(
        folder=Path("/manga/Series B"),
        title="Series B",
        english_title=None,
    )
    entry2.mu_title = "Same Series"

    entry3 = MangaEntry(
        folder=Path("/manga/Series C"),
        title="Series C",
        english_title=None,
    )
    entry3.mu_title = "Same Series"

    model = MangaTableModel([entry1, entry2, entry3])

    # Each entry should report the other two as duplicates
    assert model.get_duplicate_rows(0) == [1, 2]
    assert model.get_duplicate_rows(1) == [0, 2]
    assert model.get_duplicate_rows(2) == [0, 1]


def test_single_entry_no_duplicates():
    """Single entry is never a duplicate."""
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = "Only Series"

    model = MangaTableModel([entry1])

    assert model.is_duplicate(0) is False
    assert model.get_duplicate_rows(0) == []


def test_rebuild_on_set_entries():
    """Dupe map should rebuild when entries are set."""
    entry1 = MangaEntry(
        folder=Path("/manga/Series A"),
        title="Series A",
        english_title=None,
    )
    entry1.mu_title = "Series A"

    model = MangaTableModel([entry1])
    assert model.is_duplicate(0) is False

    # Add a duplicate
    entry2 = MangaEntry(
        folder=Path("/manga/Series B"),
        title="Series B",
        english_title=None,
    )
    entry2.mu_title = "Series A"  # Same as entry1

    model.set_entries([entry1, entry2])

    # Now both should be duplicates
    assert model.is_duplicate(0) is True
    assert model.is_duplicate(1) is True
