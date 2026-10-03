"""Shared helpers for the headless runner tests (no Qt anywhere in this folder)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from manga_list.headless.schedule import UTC


def zone(name: str):
    """An IANA zone, or skip when the platform has no tz database (Windows without tzdata)."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        pytest.skip(f"time zone database not available for {name}")


class FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now = self.now + timedelta(**kw)


@pytest.fixture
def clock():
    return FakeClock(datetime(2026, 6, 1, 12, 0, tzinfo=UTC))
