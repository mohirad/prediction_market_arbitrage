from datetime import timedelta, timezone

import pytest

from arb.timeutil import end_of_day_utc, parse_iso_utc, same_instant, unix_seconds_to_utc


def test_parse_iso_utc_z_suffix():
    dt = parse_iso_utc("2026-03-15T12:00:00Z")
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 12


def test_parse_iso_utc_explicit_offset_converted_to_utc():
    dt = parse_iso_utc("2026-03-15T08:00:00-04:00")
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 12  # 08:00 -04:00 == 12:00 UTC


def test_parse_iso_utc_rejects_naive():
    with pytest.raises(ValueError):
        parse_iso_utc("2026-03-15T12:00:00")


def test_unix_seconds_to_utc():
    dt = unix_seconds_to_utc(0)
    assert dt.year == 1970 and dt.month == 1 and dt.day == 1
    assert dt.tzinfo == timezone.utc


def test_end_of_day_utc_eastern_standard_time():
    # Jan 15 is EST (UTC-5); "end of Jan 15 ET" = Jan 16 00:00 ET = 05:00 UTC
    dt = end_of_day_utc("2026-01-15", "America/New_York")
    assert dt.isoformat() == "2026-01-16T05:00:00+00:00"


def test_end_of_day_utc_eastern_daylight_time():
    # Jul 15 is EDT (UTC-4); "end of Jul 15 ET" = Jul 16 00:00 ET = 04:00 UTC
    dt = end_of_day_utc("2026-07-15", "America/New_York")
    assert dt.isoformat() == "2026-07-16T04:00:00+00:00"


def test_same_instant_within_tolerance():
    a = parse_iso_utc("2026-03-15T12:00:00Z")
    b = parse_iso_utc("2026-03-15T12:00:30Z")
    assert same_instant(a, b, tolerance=timedelta(minutes=1))
    assert not same_instant(a, b, tolerance=timedelta(seconds=10))


def test_same_instant_rejects_naive():
    a = parse_iso_utc("2026-03-15T12:00:00Z")
    import datetime as dt_module
    naive = dt_module.datetime(2026, 3, 15, 12, 0, 0)
    with pytest.raises(ValueError):
        same_instant(a, naive)
