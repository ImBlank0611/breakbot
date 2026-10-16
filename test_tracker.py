from datetime import datetime
from zoneinfo import ZoneInfo

from tracker import BreakTracker, fmt

MNL = ZoneInfo("Asia/Manila")
G, U = 1, 42


def ts(h, m=0, s=0, day=16):
    return datetime(2026, 10, day, h, m, s, tzinfo=MNL).timestamp()


def fresh():
    return BreakTracker(":memory:", 30 * 60, "Asia/Manila")


def test_chunks_add_up():
    t = fresh()
    assert t.start_break(G, U, ts(9)).remaining == 1800
    r = t.end_break(G, U, ts(9, 10))
    assert (r.duration, r.remaining, r.breaks_today) == (600, 1200, 1)
    t.start_break(G, U, ts(12))
    r = t.end_break(G, U, ts(12, 15, 30))
    assert r.remaining == 270 and fmt(r.remaining) == "04:30" and not r.over


def test_over_limit():
    t = fresh()
    t.start_break(G, U, ts(9))
    r = t.end_break(G, U, ts(9, 34, 12))
    assert r.over and fmt(r.remaining) == "04:12"


def test_double_break_and_back():
    t = fresh()
    t.start_break(G, U, ts(9))
    assert t.start_break(G, U, ts(9, 5)).already_on_break
    t.end_break(G, U, ts(9, 6))
    assert t.end_break(G, U, ts(9, 7)).not_on_break


def test_resets_at_manila_midnight():
    t = fresh()
    t.start_break(G, U, ts(23, 30, day=16))
    t.end_break(G, U, ts(23, 55, day=16))          # 25 min on the 16th
    r = t.start_break(G, U, ts(0, 5, day=17))       # new Manila day
    assert r.remaining == 1800


def test_break_across_midnight_counts_for_start_day():
    t = fresh()
    t.start_break(G, U, ts(23, 50, day=16))
    r = t.end_break(G, U, ts(0, 10, day=17))
    assert r.used == 1200
    assert t.used_seconds(G, U, "2026-10-17") == 0


def test_alert_once_and_reset():
    t = fresh()
    day = t.day_key(ts(9))
    assert not t.was_alerted(G, U, day)
    t.mark_alerted(G, U, day)
    t.mark_alerted(G, U, day)
    assert t.was_alerted(G, U, day)
    t.start_break(G, U, ts(9))
    t.end_break(G, U, ts(9, 20))
    t.reset_user_day(G, U, day)
    assert t.used_seconds(G, U, day) == 0 and not t.was_alerted(G, U, day)


def test_users_are_independent():
    t = fresh()
    t.start_break(G, 1, ts(9))
    t.start_break(G, 2, ts(9))
    t.end_break(G, 1, ts(9, 5))
    assert len(t.open_breaks()) == 1
    assert t.used_seconds(G, 1, "2026-10-16") == 300


def test_reminder_bookkeeping():
    t = fresh()
    t.start_break(G, U, ts(9))
    bid = t.open_breaks()[0][0]
    assert t.get_reminder(bid) is None
    t.save_reminder(bid, 555, 777)
    assert t.get_reminder(bid) == (555, 777, 0)
    t.finish_reminder(bid)
    assert t.get_reminder(bid)[2] == 1


def test_announcements_only_once():
    t = fresh()
    assert not t.was_announced("v2")
    t.mark_announced("v2")
    t.mark_announced("v2")
    assert t.was_announced("v2") and not t.was_announced("v3")
