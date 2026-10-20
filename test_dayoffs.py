import sqlite3
from datetime import date

import pytest

from dayoffs import DayOffs, date_label, parse_date, span_label

TODAY = date(2026, 10, 20)  # a Tuesday
G = 1


def fresh():
    return DayOffs(sqlite3.connect(":memory:"))


@pytest.mark.parametrize("text,expected", [
    ("today", date(2026, 10, 20)),
    ("tomorrow", date(2026, 10, 21)),
    ("sat", date(2026, 10, 24)),
    ("Saturday", date(2026, 10, 24)),
    ("tue", date(2026, 10, 20)),          # today counts
    ("2026-10-25", date(2026, 10, 25)),
    ("10/25", date(2026, 10, 25)),
    ("10/25/2026", date(2026, 10, 25)),
    ("Oct 25", date(2026, 10, 25)),
    ("october 25, 2027", date(2027, 10, 25)),
    ("25 oct", date(2026, 10, 25)),
    ("jan 3", date(2027, 1, 3)),          # already passed this year → next year
    ("sept 1", date(2027, 9, 1)),
])
def test_parse_date(text, expected):
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize("text", ["nonsense", "13/40", "feb 30", "oct", ""])
def test_parse_date_rejects(text):
    with pytest.raises(ValueError):
        parse_date(text, TODAY)


def test_end_read_relative_to_start():
    start = parse_date("dec 30", TODAY)
    assert parse_date("jan 2", start) == date(2027, 1, 2)


def test_add_range_and_duplicates():
    d = fresh()
    new = d.add(G, 5, date(2026, 10, 23), date(2026, 10, 24), added_by=9, note="VL")
    assert new == [date(2026, 10, 23), date(2026, 10, 24)]
    assert d.add(G, 5, date(2026, 10, 24), date(2026, 10, 24)) == []
    assert d.is_off(G, 5, date(2026, 10, 23))


def test_between_groups_by_day():
    d = fresh()
    d.add(G, 5, date(2026, 10, 23), date(2026, 10, 23))
    d.add(G, 6, date(2026, 10, 23), date(2026, 10, 23), note="SL")
    d.add(G, 7, date(2026, 11, 30), date(2026, 11, 30))     # outside window
    d.add(2, 8, date(2026, 10, 23), date(2026, 10, 23))     # other server
    got = d.between(G, date(2026, 10, 20), date(2026, 10, 26))
    assert got == {date(2026, 10, 23): [(5, None), (6, "SL")]}


def test_remove_and_upcoming():
    d = fresh()
    d.add(G, 5, date(2026, 10, 19), date(2026, 10, 25))
    gone = d.remove(G, 5, date(2026, 10, 21), date(2026, 10, 22))
    assert gone == [date(2026, 10, 21), date(2026, 10, 22)]
    days = [r[0].day for r in d.upcoming(G, TODAY, user_id=5)]
    assert days == [20, 23, 24, 25]                          # past day 19 hidden


def test_range_limits():
    d = fresh()
    with pytest.raises(ValueError):
        d.add(G, 5, date(2026, 10, 25), date(2026, 10, 24))
    with pytest.raises(ValueError):
        d.add(G, 5, date(2026, 10, 1), date(2026, 12, 1))


def test_labels():
    assert date_label(date(2026, 10, 24)) == "Sat Oct 24"
    assert span_label(date(2026, 10, 23), date(2026, 10, 24)) == "Fri Oct 23 – Sat Oct 24"


def test_calendar_image_renders():
    from calendar_image import render
    from datetime import timedelta
    days = [TODAY + timedelta(days=i) for i in range(7)]
    png = render(days, {days[3]: [("Ben", None), ("A very long display name", "SL")]},
                 TODAY, "Day-offs")
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
