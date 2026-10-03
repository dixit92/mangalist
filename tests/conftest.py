"""Every test gets its own data folder, so no test reads or writes the user's real settings,
cache or logs (and none writes next to the source tree)."""

from __future__ import annotations

import pytest

from mangalist import paths


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_DATA_DIR, str(tmp_path / "data"))
