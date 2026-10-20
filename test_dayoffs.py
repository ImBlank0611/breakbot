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


T4, T8 = 400, 800      # two team channels


def test_add_range_and_duplicates():
    d = fresh()
    new = d.add(G, T4, 5, date(2026, 10, 23), date(2026, 10, 24), added_by=9, note="VL")
    assert new == [date(2026, 10, 23), date(2026, 10, 24)]
    assert d.add(G, T4, 5, date(2026, 10, 24), date(2026, 10, 24)) == []
    assert d.is_off(T4, 5, date(2026, 10, 23))
    assert d.teams() == [(T4, G)]


def test_teams_are_separate():
    d = fresh()
    d.add(G, T4, 5, date(2026, 10, 23), date(2026, 10, 23))
    d.add(G, T4, 6, date(2026, 10, 23), date(2026, 10, 23), note="SL")
    d.add(G, T8, 7, date(2026, 10, 23), date(2026, 10, 23))
    d.add(G, T4, 8, date(2026, 11, 30), date(2026, 11, 30))     # outside window
    assert d.between(T4, date(2026, 10, 20), date(2026, 10, 26)) == \
        {date(2026, 10, 23): [(5, None), (6, "SL")]}
    assert d.between(T8, date(2026, 10, 20), date(2026, 10, 26)) == \
        {date(2026, 10, 23): [(7, None)]}
    assert sorted(d.teams()) == [(T4, G), (T8, G)]
    assert d.remove(T8, 5, date(2026, 10, 23), date(2026, 10, 23)) == []  # not in T8


def test_remove_and_upcoming():
    d = fresh()
    d.add(G, T4, 5, date(2026, 10, 19), date(2026, 10, 25))
    gone = d.remove(T4, 5, date(2026, 10, 21), date(2026, 10, 22))
    assert gone == [date(2026, 10, 21), date(2026, 10, 22)]
    days = [r[0].day for r in d.upcoming(T4, TODAY, user_id=5)]
    assert days == [20, 23, 24, 25]                          # past day 19 hidden


def test_range_limits():
    d = fresh()
    with pytest.raises(ValueError):
        d.add(G, T4, 5, date(2026, 10, 25), date(2026, 10, 24))
    with pytest.raises(ValueError):
        d.add(G, T4, 5, date(2026, 10, 1), date(2026, 12, 1))


def test_migrates_old_single_calendar():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    db.execute("INSERT INTO settings VALUES ('dayoff_channel_id', '400')")
    db.execute("""CREATE TABLE dayoffs (guild_id INTEGER, user_id INTEGER, day TEXT,
                  note TEXT, added_by INTEGER, added_ts REAL,
                  PRIMARY KEY (guild_id, user_id, day))""")
    db.execute("INSERT INTO dayoffs VALUES (1, 5, '2026-10-23', 'VL', 9, 1.0)")
    db.commit()
    d = DayOffs(db)
    assert d.between(T4, date(2026, 10, 20), date(2026, 10, 26)) == \
        {date(2026, 10, 23): [(5, "VL")]}
    assert d.teams() == [(T4, 1)]
    DayOffs(db)                                              # second start is a no-op
    assert d.teams() == [(T4, 1)]


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
