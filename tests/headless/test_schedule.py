"""Schedule maths: parsing, daily wall-clock times across DST, elapsed-time intervals."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from mangalist.headless.schedule import UTC, DailyAt, EveryHours, WeeklyAt, is_missed, parse_schedule, validate_schedule

from .conftest import zone


@pytest.mark.parametrize("text, expected", [
    ("daily@03:30", DailyAt(3, 30)),
    ("Daily 3:05", DailyAt(3, 5)),
    ("daily:23:59", DailyAt(23, 59)),
    ("04:00", DailyAt(4, 0)),
    ("every 12h", EveryHours(12)),
    ("every:6h", EveryHours(6)),
    ("EVERY 1 hour", EveryHours(1)),
    ("24h", EveryHours(24)),
    ("every 1.5h", EveryHours(1.5)),
    ("off", None),
    ("", None),
    (None, None),
    ("disabled", None),
    ("weekly@sun 03:30", WeeklyAt(6, 3, 30)),
    ("Weekly Wednesday 4:05", WeeklyAt(2, 4, 5)),
    ("weekly:mon 00:00", WeeklyAt(0, 0, 0)),
])
def test_parse_schedule(text, expected):
    assert parse_schedule(text) == expected


@pytest.mark.parametrize("text", ["daily@25:00", "daily@03:60", "every 0h", "weekly", "every 12m",
                                  "3:30pm", "weekly@sun", "weekly@funday 03:30", "weekly@sun 24:00"])
def test_parse_schedule_rejects_typos(text):
    with pytest.raises(ValueError):
        parse_schedule(text)


def test_describe_round_trips():
    for s in (DailyAt(3, 30), EveryHours(12), EveryHours(1.5), WeeklyAt(6, 3, 30), WeeklyAt(0, 23, 59)):
        assert parse_schedule(s.describe()) == s


def test_daily_later_today_and_tomorrow_utc():
    s = DailyAt(3, 30)
    assert s.next_after(datetime(2026, 6, 1, 1, 0, tzinfo=UTC), UTC) == \
        datetime(2026, 6, 1, 3, 30, tzinfo=UTC)
    assert s.next_after(datetime(2026, 6, 1, 3, 30, tzinfo=UTC), UTC) == \
        datetime(2026, 6, 2, 3, 30, tzinfo=UTC), "strictly after: the slot itself is not 'next'"
    assert s.next_after(datetime(2026, 6, 1, 12, 0, tzinfo=UTC), UTC) == \
        datetime(2026, 6, 2, 3, 30, tzinfo=UTC)


def test_daily_uses_local_wall_clock_in_a_fixed_offset_zone():
    tz = timezone(timedelta(hours=5, minutes=30))
    nxt = DailyAt(3, 30).next_after(datetime(2026, 6, 1, 0, 0, tzinfo=UTC), tz)
    assert nxt.astimezone(tz).strftime("%H:%M") == "03:30"
    assert nxt == datetime(2026, 6, 1, 22, 0, tzinfo=UTC)


def test_daily_keeps_local_time_across_dst():
    berlin = zone("Europe/Berlin")
    s = DailyAt(3, 30)
    winter = s.next_after(datetime(2026, 3, 27, 12, 0, tzinfo=UTC), berlin)   # CET, UTC+1
    summer = s.next_after(datetime(2026, 3, 30, 12, 0, tzinfo=UTC), berlin)   # CEST, UTC+2
    assert winter == datetime(2026, 3, 28, 2, 30, tzinfo=UTC)
    assert summer == datetime(2026, 3, 31, 1, 30, tzinfo=UTC)
    assert winter.astimezone(berlin).strftime("%H:%M") == summer.astimezone(berlin).strftime("%H:%M")


def test_daily_time_in_the_spring_forward_gap_runs_once_after_the_gap():
    berlin = zone("Europe/Berlin")   # 2026-03-29 02:00 CET -> 03:00 CEST
    s = DailyAt(2, 30)
    nxt = s.next_after(datetime(2026, 3, 28, 12, 0, tzinfo=UTC), berlin)
    assert nxt == datetime(2026, 3, 29, 1, 30, tzinfo=UTC)
    assert nxt.astimezone(berlin).strftime("%m-%d %H:%M") == "03-29 03:30"
    # And the following day is back at 02:30 local.
    after = s.next_after(nxt, berlin)
    assert after.astimezone(berlin).strftime("%m-%d %H:%M") == "03-30 02:30"


def test_daily_time_in_the_fall_back_overlap_runs_once():
    berlin = zone("Europe/Berlin")   # 2026-10-25 03:00 CEST -> 02:00 CET: 02:30 happens twice
    s = DailyAt(2, 30)
    first = s.next_after(datetime(2026, 10, 24, 12, 0, tzinfo=UTC), berlin)
    assert first == datetime(2026, 10, 25, 0, 30, tzinfo=UTC), "first occurrence (CEST)"
    second = s.next_after(first, berlin)
    assert second.astimezone(berlin).strftime("%m-%d %H:%M") == "10-26 02:30", \
        "the repeated 02:30 (01:30 UTC) must not fire again"


def test_daily_new_york_dst():
    ny = zone("America/New_York")
    s = DailyAt(9, 0)
    days = []
    t = datetime(2026, 3, 6, 0, 0, tzinfo=UTC)
    for _ in range(4):   # across 2026-03-08
        t = s.next_after(t, ny)
        days.append(t)
    assert [d.astimezone(ny).strftime("%d %H:%M") for d in days] == \
        ["06 09:00", "07 09:00", "08 09:00", "09 09:00"]
    assert days[2] - days[1] == timedelta(hours=23)


def test_every_hours_counts_elapsed_time_across_dst():
    berlin = zone("Europe/Berlin")
    start = datetime(2026, 3, 28, 22, 0, tzinfo=UTC)
    nxt = EveryHours(12).next_after(start, berlin)
    assert nxt - start == timedelta(hours=12)


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        DailyAt(1, 0).next_after(datetime(2026, 1, 1), UTC)
    with pytest.raises(ValueError):
        EveryHours(1).next_after(datetime(2026, 1, 1), UTC)


def test_is_missed():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    assert is_missed(now - timedelta(seconds=1), now)
    assert is_missed(now, now)
    assert not is_missed(now + timedelta(seconds=1), now)
    assert not is_missed(None, now)



# --- weekly (owner, 2026-10-10: the choices are weekly, daily, every 12 hours, every 6 hours) -----------------------

def test_weekly_runs_on_its_day_at_its_local_time():
    tz = zone("America/New_York")
    sunday = WeeklyAt(6, 3, 30)
    # Friday 2026-10-09 23:00 local -> Sunday 2026-10-11 03:30 local (EDT, UTC-4)
    after = datetime(2026, 10, 10, 3, 0, tzinfo=UTC)
    assert sunday.next_after(after, tz) == datetime(2026, 10, 11, 7, 30, tzinfo=UTC)
    # exactly at the slot -> the next week
    assert sunday.next_after(datetime(2026, 10, 11, 7, 30, tzinfo=UTC), tz) == datetime(2026, 10, 18, 7, 30, tzinfo=UTC)
    # later the same Sunday -> the next week
    assert sunday.next_after(datetime(2026, 10, 11, 12, 0, tzinfo=UTC), tz) == datetime(2026, 10, 18, 7, 30, tzinfo=UTC)


def test_weekly_keeps_its_wall_clock_time_across_the_fall_back():
    tz = zone("America/New_York")
    # DST ends Sunday 2026-11-01: 03:30 local is EST (UTC-5) from then on
    nxt = WeeklyAt(6, 3, 30).next_after(datetime(2026, 10, 26, 12, 0, tzinfo=UTC), tz)
    assert nxt == datetime(2026, 11, 1, 8, 30, tzinfo=UTC)
    assert nxt.astimezone(tz).strftime("%a %H:%M") == "Sun 03:30"


def test_validate_stores_weekly_in_its_canonical_form():
    assert validate_schedule("Weekly Wednesday 4:05") == "weekly@wed 04:05"
