"""Break bookkeeping — pure logic + SQLite, no Discord code here.

Every break belongs to the calendar day (in the configured timezone) on which
it STARTED. Each person gets `limit_seconds` of break per day, which they can
spend in as many chunks as they like.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo


@dataclass
class StartResult:
    ok: bool
    already_on_break: bool = False
    remaining: float = 0.0          # seconds left at the moment the break starts
    used: float = 0.0


@dataclass
class EndResult:
    ok: bool
    not_on_break: bool = False
    duration: float = 0.0           # length of the break that just ended
    used: float = 0.0               # total used today (incl. this one)
    remaining: float = 0.0          # limit - used (negative = over)
    breaks_today: int = 0

    @property
    def over(self) -> bool:
        return self.remaining < 0


@dataclass
class UserDay:
    user_id: int
    used: float
    breaks: int
    on_break_since: float | None


class BreakTracker:
    def __init__(self, db_path: str, limit_seconds: int = 30 * 60,
                 timezone: str = "Asia/Manila"):
        self.limit = limit_seconds
        self.tz = ZoneInfo(timezone)
        self.db = sqlite3.connect(db_path)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS breaks (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id  INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                day       TEXT    NOT NULL,
                start_ts  REAL    NOT NULL,
                end_ts    REAL
            )""")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                guild_id INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                day      TEXT    NOT NULL,
                PRIMARY KEY (guild_id, user_id, day)
            )""")
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                break_id   INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                done       INTEGER NOT NULL DEFAULT 0
            )""")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_breaks_user_day "
            "ON breaks (guild_id, user_id, day)")
        self.db.commit()

    # ── helpers ────────────────────────────────────────────────────────────
    def day_key(self, ts: float | None = None) -> str:
        ts = time.time() if ts is None else ts
        return datetime.fromtimestamp(ts, self.tz).date().isoformat()

    def active_break(self, guild_id: int, user_id: int):
        """(id, day, start_ts) of the open break, or None."""
        return self.db.execute(
            "SELECT id, day, start_ts FROM breaks "
            "WHERE guild_id=? AND user_id=? AND end_ts IS NULL",
            (guild_id, user_id)).fetchone()

    def used_seconds(self, guild_id: int, user_id: int, day: str,
                     now: float | None = None) -> float:
        now = time.time() if now is None else now
        rows = self.db.execute(
            "SELECT start_ts, end_ts FROM breaks "
            "WHERE guild_id=? AND user_id=? AND day=?",
            (guild_id, user_id, day)).fetchall()
        return sum((end if end is not None else now) - start
                   for start, end in rows)

    # ── actions ────────────────────────────────────────────────────────────
    def start_break(self, guild_id: int, user_id: int,
                    now: float | None = None) -> StartResult:
        now = time.time() if now is None else now
        if self.active_break(guild_id, user_id):
            day = self.active_break(guild_id, user_id)[1]
            used = self.used_seconds(guild_id, user_id, day, now)
            return StartResult(ok=False, already_on_break=True,
                               remaining=self.limit - used, used=used)
        day = self.day_key(now)
        used = self.used_seconds(guild_id, user_id, day, now)
        self.db.execute(
            "INSERT INTO breaks (guild_id, user_id, day, start_ts) "
            "VALUES (?, ?, ?, ?)", (guild_id, user_id, day, now))
        self.db.commit()
        return StartResult(ok=True, remaining=self.limit - used, used=used)

    def end_break(self, guild_id: int, user_id: int,
                  now: float | None = None) -> EndResult:
        now = time.time() if now is None else now
        active = self.active_break(guild_id, user_id)
        if not active:
            day = self.day_key(now)
            used = self.used_seconds(guild_id, user_id, day, now)
            return EndResult(ok=False, not_on_break=True, used=used,
                             remaining=self.limit - used)
        break_id, day, start_ts = active
        self.db.execute("UPDATE breaks SET end_ts=? WHERE id=?", (now, break_id))
        self.db.commit()
        used = self.used_seconds(guild_id, user_id, day, now)
        count = self.db.execute(
            "SELECT COUNT(*) FROM breaks WHERE guild_id=? AND user_id=? AND day=?",
            (guild_id, user_id, day)).fetchone()[0]
        return EndResult(ok=True, duration=now - start_ts, used=used,
                         remaining=self.limit - used, breaks_today=count)

    def reset_user_day(self, guild_id: int, user_id: int, day: str | None = None):
        day = day or self.day_key()
        # Finished breaks are wiped; a break still in progress restarts from now.
        self.db.execute("DELETE FROM breaks WHERE guild_id=? AND user_id=? AND day=? "
                        "AND end_ts IS NOT NULL", (guild_id, user_id, day))
        self.db.execute("UPDATE breaks SET start_ts=? WHERE guild_id=? AND user_id=? "
                        "AND day=? AND end_ts IS NULL",
                        (time.time(), guild_id, user_id, day))
        self.db.execute("DELETE FROM alerts WHERE guild_id=? AND user_id=? AND day=?",
                        (guild_id, user_id, day))
        self.db.commit()

    # ── over-limit alert bookkeeping (so managers are pinged once per day) ─
    def was_alerted(self, guild_id: int, user_id: int, day: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM alerts WHERE guild_id=? AND user_id=? AND day=?",
            (guild_id, user_id, day)).fetchone() is not None

    def mark_alerted(self, guild_id: int, user_id: int, day: str):
        self.db.execute("INSERT OR IGNORE INTO alerts VALUES (?, ?, ?)",
                        (guild_id, user_id, day))
        self.db.commit()

    # ── 1-minute reminder bookkeeping (one reminder message per break) ─────
    def get_reminder(self, break_id: int):
        """(channel_id, message_id, done) or None."""
        return self.db.execute(
            "SELECT channel_id, message_id, done FROM reminders WHERE break_id=?",
            (break_id,)).fetchone()

    def save_reminder(self, break_id: int, channel_id: int, message_id: int):
        self.db.execute("INSERT OR REPLACE INTO reminders VALUES (?, ?, ?, 0)",
                        (break_id, channel_id, message_id))
        self.db.commit()

    def finish_reminder(self, break_id: int):
        self.db.execute("UPDATE reminders SET done=1 WHERE break_id=?", (break_id,))
        self.db.commit()

    # ── reporting ──────────────────────────────────────────────────────────
    def open_breaks(self):
        """All currently open breaks: (break_id, guild_id, user_id, day, start_ts)."""
        return self.db.execute(
            "SELECT id, guild_id, user_id, day, start_ts FROM breaks "
            "WHERE end_ts IS NULL").fetchall()

    def day_summary(self, guild_id: int, day: str | None = None,
                    now: float | None = None) -> list[UserDay]:
        now = time.time() if now is None else now
        day = day or self.day_key(now)
        users = [r[0] for r in self.db.execute(
            "SELECT DISTINCT user_id FROM breaks WHERE guild_id=? AND day=?",
            (guild_id, day))]
        out = []
        for uid in users:
            n = self.db.execute(
                "SELECT COUNT(*) FROM breaks WHERE guild_id=? AND user_id=? AND day=?",
                (guild_id, uid, day)).fetchone()[0]
            act = self.active_break(guild_id, uid)
            out.append(UserDay(uid, self.used_seconds(guild_id, uid, day, now), n,
                               act[2] if act and act[1] == day else None))
        out.sort(key=lambda u: -u.used)
        return out


def fmt(seconds: float) -> str:
    """Seconds → 'MM:SS' (absolute value; caller decides on 'over')."""
    s = int(round(abs(seconds)))
    return f"{s // 60:02d}:{s % 60:02d}"
