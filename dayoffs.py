"""Day-off calendar — pure logic + SQLite, no Discord code here.

QC records who is off on which dates; the bot posts a rolling 7-day calendar
at every shift start, so a scheduled day-off shows up 7 days ahead.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MAX_RANGE_DAYS = 31

_MONTHS = {}
for _i in range(1, 13):
    _MONTHS[calendar.month_name[_i].lower()] = _i
    _MONTHS[calendar.month_abbr[_i].lower()] = _i
_MONTHS["sept"] = 9

_WEEKDAYS = {}
for _i in range(7):
    _WEEKDAYS[calendar.day_name[_i].lower()] = _i
    _WEEKDAYS[calendar.day_abbr[_i].lower()] = _i
_WEEKDAYS.update({"tues": 1, "weds": 2, "thur": 3, "thurs": 3})


def parse_date(text: str, today: date) -> date:
    """Read a date the way QC would type it. No year → the next time that date
    comes around (today counts). Raises ValueError if it can't be read.

    Accepts: today, tomorrow, sat / saturday (next one, today counts),
    2026-10-25, 10/25, 10/25/2026, oct 25, october 25 2026, 25 oct.
    """
    s = re.sub(r"[,.]", " ", text.strip().lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s == "today":
        return today
    if s in ("tomorrow", "tmr", "tom", "tmrw"):
        return today + timedelta(days=1)
    if s in _WEEKDAYS:
        return today + timedelta(days=(_WEEKDAYS[s] - today.weekday()) % 7)

    year = month = day = None
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s):
        year, month, day = map(int, m.groups())
    elif m := re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?", s):
        month, day = int(m[1]), int(m[2])
        if m[3]:
            year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
    elif m := re.fullmatch(r"([a-z]+) (\d{1,2})(?: (\d{4}))?", s):
        if m[1] not in _MONTHS:
            raise ValueError(text)
        month, day, year = _MONTHS[m[1]], int(m[2]), int(m[3]) if m[3] else None
    elif m := re.fullmatch(r"(\d{1,2}) ([a-z]+)(?: (\d{4}))?", s):
        if m[2] not in _MONTHS:
            raise ValueError(text)
        day, month, year = int(m[1]), _MONTHS[m[2]], int(m[3]) if m[3] else None
    else:
        raise ValueError(text)

    if year is not None:
        return date(year, month, day)          # ValueError if impossible
    d = date(today.year, month, day)
    if d < today:
        d = date(today.year + 1, month, day)
    return d


def date_label(d: date) -> str:
    """'Sat Oct 25'"""
    return f"{d:%a %b} {d.day}"


def span_label(start: date, end: date) -> str:
    return date_label(start) if start == end else f"{date_label(start)} – {date_label(end)}"


class DayOffs:
    def __init__(self, db: sqlite3.Connection, timezone: str = "Asia/Manila"):
        self.db = db
        self.tz = ZoneInfo(timezone)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS dayoffs (
                guild_id  INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                day       TEXT    NOT NULL,
                note      TEXT,
                added_by  INTEGER,
                added_ts  REAL    NOT NULL,
                PRIMARY KEY (guild_id, user_id, day)
            )""")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""")
        self.db.commit()

    def get_setting(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?",
                              (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
        self.db.commit()

    def today(self, now: float | None = None) -> date:
        now = time.time() if now is None else now
        return datetime.fromtimestamp(now, self.tz).date()

    @staticmethod
    def _days(start: date, end: date) -> list[date]:
        if end < start:
            raise ValueError("end before start")
        n = (end - start).days + 1
        if n > MAX_RANGE_DAYS:
            raise ValueError("range too long")
        return [start + timedelta(days=i) for i in range(n)]

    def add(self, guild_id: int, user_id: int, start: date, end: date,
            added_by: int | None = None, note: str | None = None) -> list[date]:
        """Add day-offs; returns the dates that were newly added. Re-adding an
        existing date just updates its note."""
        new = []
        for d in self._days(start, end):
            cur = self.db.execute(
                "INSERT OR IGNORE INTO dayoffs VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, user_id, d.isoformat(), note, added_by, time.time()))
            if cur.rowcount:
                new.append(d)
            elif note:
                self.db.execute(
                    "UPDATE dayoffs SET note=? WHERE guild_id=? AND user_id=? AND day=?",
                    (note, guild_id, user_id, d.isoformat()))
        self.db.commit()
        return new

    def remove(self, guild_id: int, user_id: int, start: date, end: date) -> list[date]:
        gone = []
        for d in self._days(start, end):
            cur = self.db.execute(
                "DELETE FROM dayoffs WHERE guild_id=? AND user_id=? AND day=?",
                (guild_id, user_id, d.isoformat()))
            if cur.rowcount:
                gone.append(d)
        self.db.commit()
        return gone

    def between(self, guild_id: int, start: date, end: date) -> dict[date, list[tuple[int, str | None]]]:
        """{date: [(user_id, note), ...]} for every date in the range that has offs."""
        out: dict[date, list] = defaultdict(list)
        for uid, day, note in self.db.execute(
                "SELECT user_id, day, note FROM dayoffs "
                "WHERE guild_id=? AND day BETWEEN ? AND ? ORDER BY day, added_ts",
                (guild_id, start.isoformat(), end.isoformat())):
            out[date.fromisoformat(day)].append((uid, note))
        return dict(out)

    def upcoming(self, guild_id: int, from_day: date, user_id: int | None = None,
                 limit: int = 50) -> list[tuple[date, int, str | None]]:
        """[(date, user_id, note)] from `from_day` on, soonest first."""
        q = "SELECT day, user_id, note FROM dayoffs WHERE guild_id=? AND day>=?"
        args: list = [guild_id, from_day.isoformat()]
        if user_id is not None:
            q += " AND user_id=?"
            args.append(user_id)
        q += " ORDER BY day, added_ts LIMIT ?"
        args.append(limit)
        return [(date.fromisoformat(d), u, n) for d, u, n in self.db.execute(q, args)]

    def is_off(self, guild_id: int, user_id: int, day: date) -> bool:
        return self.db.execute(
            "SELECT 1 FROM dayoffs WHERE guild_id=? AND user_id=? AND day=?",
            (guild_id, user_id, day.isoformat())).fetchone() is not None
