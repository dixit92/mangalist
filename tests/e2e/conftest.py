"""End-to-end tests: a fresh database per test (the data folder is the test's own: tests/conftest.py)."""

from __future__ import annotations

import pytest

from mangalist import store


@pytest.fixture
def db() -> store.Store:
    store.reset_stores()
    return store.get_store()
