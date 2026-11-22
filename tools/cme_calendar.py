#!/usr/bin/env python3
"""
The CME equity index futures trading calendar.

Used to tell a genuine data gap apart from a scheduled early close.  The
rules cover the holidays of the CME equity index complex (ES, MES, NQ, ...):

*   full closures - New Year's Day, Martin Luther King Jr. Day, Presidents
    Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day,
    Thanksgiving and Christmas
*   early closes at 12:00 CT (13:00 Eastern) - the Friday after Thanksgiving,
    Christmas Eve, New Year's Eve, July 3rd and Good Friday

The early close time is the reason the short session spans roughly 19 hours
instead of 23.
"""

from __future__ import annotations

import datetime as dt

# The early close of the CME equity index complex is 12:00 CT.
EARLY_CLOSE_ET = dt.time(13, 0)
EARLY_CLOSE_CT = dt.time(12, 0)


def easter(year: int) -> dt.date:
    """Easter Sunday, anonymous Gregorian computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(year, month, day + 1)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> dt.date:
    """The n-th given weekday of a month (n = 1..5, 5 = last)."""
    if n < 5:
        d = dt.date(year, month, 1)
        offset = (weekday - d.weekday()) % 7
        return d + dt.timedelta(days=offset + 7 * (n - 1))

    return last_weekday(year, month, weekday)


def last_weekday(year: int, month: int, weekday: int) -> dt.date:
    """The last given weekday of a month."""
    if month == 12:
        d = dt.date(year, 12, 31)
    else:
        d = dt.date(year, month + 1, 1) - dt.timedelta(days=1)

    offset = (d.weekday() - weekday) % 7
    return d - dt.timedelta(days=offset)


def preceding_business_day(d: dt.date, excluded: set[dt.date]) -> dt.date:
    """The last business day on or before a date, skipping weekends and holidays."""
    while d.weekday() >= 5 or d in excluded:
        d -= dt.timedelta(days=1)
    return d


def observed(fixed: dt.date) -> dt.date:
    """A fixed date holiday moves to the nearest weekday."""
    if fixed.weekday() == 5:      # Saturday
        return fixed - dt.timedelta(days=1)
    if fixed.weekday() == 6:      # Sunday
        return fixed + dt.timedelta(days=1)
    return fixed


def holidays(year: int) -> set[dt.date]:
    """Full closures of the CME equity index complex for a calendar year.

    The equity index complex (ES, MES, NQ, ...) is closed on New Year's Day,
    Good Friday and Christmas.  All the other federal holidays are early
    closes, not closures - that was verified against the exported data.
    """
    out = {
        observed(dt.date(year, 1, 1)),            # New Year's Day
        easter(year) - dt.timedelta(days=2),      # Good Friday
        observed(dt.date(year, 12, 25)),          # Christmas
    }
    return out


def early_closes(year: int) -> set[dt.date]:
    """Early close days of the CME equity index complex for a calendar year.

    The equity index complex closes early at 12:00 CT (13:00 Eastern) on
    every other federal holiday and on the eves of Christmas and New Year.
    """
    hol = holidays(year)
    thanksgiving = nth_weekday(year, 11, 3, 4)

    out = {
        nth_weekday(year, 1, 0, 3),               # Martin Luther King Jr. Day
        nth_weekday(year, 2, 0, 3),               # Presidents Day
        last_weekday(year, 5, 0),                 # Memorial Day
        observed(dt.date(year, 6, 19)),           # Juneteenth
        last_weekday(year, 9, 0),                 # Labor Day
        thanksgiving,                              # Thanksgiving
        thanksgiving + dt.timedelta(days=1),       # the Friday after
    }

    # Independence Day, with the early close on the preceding business day
    # when the holiday itself falls on a weekend.
    independence = dt.date(year, 7, 4)
    if independence.weekday() < 5 and independence not in hol:
        out.add(independence)
    else:
        out.add(preceding_business_day(dt.date(year, 7, 3), hol))

    # Christmas Eve and New Year's Eve, with the same weekend rule.
    for nominal in (dt.date(year, 12, 24), dt.date(year, 12, 31)):
        d = nominal
        if d.weekday() >= 5 or d in hol:
            d = preceding_business_day(nominal, hol)
        if d not in hol:
            out.add(d)

    return out


def calendar(year: int) -> dict[dt.date, str]:
    """Every non-trading date of a year with its kind."""
    out: dict[dt.date, str] = {}

    for d in holidays(year):
        out[d] = "holiday"

    for d in early_closes(year):
        out.setdefault(d, "early_close")

    return out


if __name__ == "__main__":  # pragma: no cover
    import sys

    for year in (int(a) for a in sys.argv[1:]) or [2026]:
        print(f"--- {year}")
        for d, kind in sorted(calendar(year).items()):
            print(f"  {d} {d.strftime('%a')} {kind}")
