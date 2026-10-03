"""Inventory tests: a fresh database in the per-test data folder and synthetic library trees."""

from __future__ import annotations

import pytest

from mangalist import store


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()


@pytest.fixture
def library(tmp_path):
    """A synthetic library root (every name in these tests is made up)."""
    root = tmp_path / "library" / "Manga"
    root.mkdir(parents=True)
    return root


def make_archive(path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
