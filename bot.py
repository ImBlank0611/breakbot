"""Breakroom bot — 30 minutes of break per person per day, announced publicly.

Type `break` / `back` in the breakroom channel (or use /break and /back).
Managers get pinged when someone runs past the daily limit.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import tasks

from changelog import UPDATES
from dayoffs import MAX_RANGE_DAYS, DayOffs, date_label, parse_date, span_label
from tracker import BreakTracker, fmt

# ── configuration (set these as Railway variables) ─────────────────────────
TOKEN = os.environ["DISCORD_TOKEN"]
BREAK_CHANNEL_ID = int(os.environ["BREAK_CHANNEL_ID"])
MANAGER_ROLE_ID = int(os.environ.get("MANAGER_ROLE_ID", "0") or 0)
LIMIT_MINUTES = int(os.environ.get("BREAK_LIMIT_MINUTES", "30"))
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Manila")
DATA_DIR = os.environ.get("DATA_DIR", "/data")
REMINDER_SECONDS = int(os.environ.get("REMINDER_SECONDS", "60"))  # 0 = off

# Day-off calendar
QC_ROLE_ID = int(os.environ.get("QC_ROLE_ID", "1227825483492888676") or 0)
DAYOFF_CHANNEL_ID = int(os.environ.get("DAYOFF_CHANNEL_ID", "0") or 0) or BREAK_CHANNEL_ID
DAYOFF_POST_TIMES = os.environ.get("DAYOFF_POST_TIMES", "08:00,16:00,00:00")
DAYOFF_DAYS_AHEAD = 7

BREAK_WORDS = {"break", "brb", "on break", "going on break"}
BACK_WORDS = {"back", "im back", "i'm back", "i’m back", "back from break"}

YELLOW, GREEN, RED, BLUE, ORANGE = 0xF1C40F, 0x2ECC71, 0xE74C3C, 0x5865F2, 0xE67E22

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("breakbot")


def open_db() -> BreakTracker:
    data = Path(DATA_DIR)
    try:
        data.mkdir(parents=True, exist_ok=True)
        (data / ".write-test").touch()
    except OSError:
        data = Path(__file__).parent / "data"
        data.mkdir(exist_ok=True)
        log.warning("DATA_DIR %s not writable; using %s. Break history will be "
                    "LOST on redeploy — attach a Railway volume at /data.",
                    DATA_DIR, data)
    return BreakTracker(str(data / "breaks.db"), LIMIT_MINUTES * 60, TIMEZONE)


tracker = open_db()
dayoffs = DayOffs(tracker.db, TIMEZONE)

intents = discord.Intents.default()
intents.message_content = True          # needed to read "break" / "back"
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)


# ── helpers ────────────────────────────────────────────────────────────────
def manager_ping() -> str:
    return f"<@&{MANAGER_ROLE_ID}>" if MANAGER_ROLE_ID else ""


PING_ALLOWED = discord.AllowedMentions(everyone=False, users=False, roles=True)
NO_PINGS = discord.AllowedMentions.none()


def is_manager(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_guild:
        return True
    return any(r.id == MANAGER_ROLE_ID for r in member.roles)


def left_text(remaining: float) -> str:
    return (f"**{fmt(remaining)}** minutes" if remaining >= 0
            else f"**00:00** — **{fmt(remaining)} over** the limit")


def break_embed(member: discord.Member, remaining: float) -> discord.Embed:
    e = discord.Embed(description=f"☕ **{member.display_name}** went on break",
                      color=YELLOW if remaining > 0 else RED)
    e.add_field(name="Break left", value=left_text(remaining))
    if remaining <= 0:
        e.set_footer(text="No break time left today")
    return e


def back_embed(member: discord.Member, res) -> discord.Embed:
    e = discord.Embed(
        description=f"✅ **{member.display_name}** is back",
        color=RED if res.over else GREEN)
    e.add_field(name="This break", value=fmt(res.duration))
    e.add_field(name="Break left", value=left_text(res.remaining))
    e.set_footer(text=f"Break #{res.breaks_today} today · "
                      f"{fmt(res.used)} of {LIMIT_MINUTES}:00 used")
    return e


async def break_channel() -> discord.abc.Messageable | None:
    ch = client.get_channel(BREAK_CHANNEL_ID)
    if ch is None:
        try:
            ch = await client.fetch_channel(BREAK_CHANNEL_ID)
        except discord.HTTPException:
            log.error("Can't reach break channel %s", BREAK_CHANNEL_ID)
            return None
    return ch


async def alert_if_over(guild: discord.Guild, user_id: int, day: str,
                        still_on_break: bool):
    """Ping managers once per person per day when they pass the limit."""
    used = tracker.used_seconds(guild.id, user_id, day)
    if used < tracker.limit or tracker.was_alerted(guild.id, user_id, day):
        return
    tracker.mark_alerted(guild.id, user_id, day)
    member = guild.get_member(user_id)
    name = member.display_name if member else f"<@{user_id}>"
    ch = await break_channel()
    if not ch:
        return
    status = "is **still on break**" if still_on_break else "went over"
    e = discord.Embed(
        description=f"⚠️ **{name}** has used all **{LIMIT_MINUTES} minutes** "
                    f"of today's break and {status}.",
        color=RED)
    e.add_field(name="Used today", value=fmt(used))
    e.add_field(name="Over by", value=fmt(used - tracker.limit))
    await ch.send(content=manager_ping() or None, embed=e,
                  allowed_mentions=PING_ALLOWED)


# ── 1-minute reminder with a live countdown ────────────────────────────────
PING_USER = discord.AllowedMentions(everyone=False, users=True, roles=False)


async def send_reminder(guild: discord.Guild, break_id: int, user_id: int,
                        remaining: float):
    """Ping the person with a countdown. Discord's <t:…:R> timestamp ticks down
    live on every screen, so the bot doesn't need to keep editing the message."""
    ch = await break_channel()
    if not ch:
        return
    ends_at = int(time.time() + remaining)
    mins = max(1, round(REMINDER_SECONDS / 60))
    e = discord.Embed(
        description=f"⏰ **{mins} minute{'s' if mins != 1 else ''} of break left** — "
                    f"time's up <t:{ends_at}:R> (<t:{ends_at}:T>)",
        color=ORANGE)
    e.set_footer(text="Type back when you return")
    msg = await ch.send(content=f"<@{user_id}>", embed=e, allowed_mentions=PING_USER)
    tracker.save_reminder(break_id, ch.id, msg.id)


async def close_reminder(break_id: int, text: str, color: int):
    """Swap the countdown for its final state (back in time / time's up)."""
    rem = tracker.get_reminder(break_id)
    if not rem or rem[2]:
        return
    tracker.finish_reminder(break_id)
    channel_id, message_id, _ = rem
    ch = client.get_channel(channel_id)
    if ch is None:
        return
    try:
        await ch.get_partial_message(message_id).edit(
            embed=discord.Embed(description=text, color=color))
    except discord.HTTPException:
        log.warning("Couldn't edit reminder %s", message_id)


# ── core actions (shared by text triggers and slash commands) ──────────────
async def do_break(member: discord.Member):
    res = tracker.start_break(member.guild.id, member.id)
    if not res.ok:
        return None, (f"You're already on break — "
                      f"{left_text(res.remaining)} left. Type `back` when you return.")
    return break_embed(member, res.remaining), None


async def do_back(member: discord.Member):
    active = tracker.active_break(member.guild.id, member.id)
    res = tracker.end_break(member.guild.id, member.id)
    if not res.ok:
        return None, (f"You're not on break, so there's nothing to end. "
                      f"Break left today: {left_text(res.remaining)}.\n"
                      f"-# If you forgot to type `break` when you left, ask a "
                      f"manager to add it with `/logbreak`.")
    break_id, day = active[0], active[1]
    await close_reminder(
        break_id,
        f"✅ Back with **{fmt(res.remaining)}** to spare" if not res.over
        else f"⏰ Time's up — came back **{fmt(res.remaining)} over**",
        GREEN if not res.over else RED)
    # Ping managers if they went over and nobody was pinged yet (e.g. bot was
    # restarting at the moment they crossed the limit).
    await alert_if_over(member.guild, member.id, day, still_on_break=False)
    return back_embed(member, res), None


# ── events ─────────────────────────────────────────────────────────────────
@client.event
async def on_ready():
    for guild in client.guilds:
        tree.copy_global_to(guild=guild)
        await tree.sync(guild=guild)
    if not watch_breaks.is_running():
        watch_breaks.start()
    if not dayoff_card.is_running():
        dayoff_card.start()
    await announce_updates()
    log.info("Logged in as %s — watching channel %s, limit %s min, tz %s",
             client.user, BREAK_CHANNEL_ID, LIMIT_MINUTES, TIMEZONE)


async def announce_updates():
    """Post any changelog entries the breakroom hasn't seen yet, oldest first."""
    pending = [u for u in UPDATES if not tracker.was_announced(u["id"])]
    if not pending:
        return
    ch = await break_channel()
    if not ch:
        return
    for u in reversed(pending):
        e = discord.Embed(title=u["title"], description=u["body"], color=BLUE)
        e.set_footer(text="Break Bot update")
        try:
            await ch.send(embed=e, allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.exception("Couldn't post update %s", u["id"])
            return
        tracker.mark_announced(u["id"])
        log.info("Announced update %s", u["id"])


@client.event
async def on_message(message: discord.Message):
    if message.author.bot or message.channel.id != BREAK_CHANNEL_ID:
        return
    text = message.content.strip().lower().rstrip("!.")
    if text in BREAK_WORDS:
        embed, err = await do_break(message.author)
    elif text in BACK_WORDS:
        embed, err = await do_back(message.author)
    else:
        return
    if err:
        await message.reply(err, mention_author=False, allowed_mentions=NO_PINGS)
    else:
        await message.channel.send(embed=embed, allowed_mentions=NO_PINGS)


@tasks.loop(seconds=5)
async def watch_breaks():
    """Send the 1-minute reminder, flip it to "time's up", and ping managers
    for anyone still on break past the limit."""
    for break_id, guild_id, user_id, day, _start in tracker.open_breaks():
        guild = client.get_guild(guild_id)
        if not guild:
            continue
        remaining = tracker.limit - tracker.used_seconds(guild_id, user_id, day)
        try:
            if (REMINDER_SECONDS and 0 < remaining <= REMINDER_SECONDS
                    and not tracker.get_reminder(break_id)):
                await send_reminder(guild, break_id, user_id, remaining)
            elif remaining <= 0:
                await close_reminder(break_id, "⏰ **Time's up** — break is over",
                                     RED)
            await alert_if_over(guild, user_id, day, still_on_break=True)
        except discord.HTTPException:
            log.exception("watch_breaks failed for user %s", user_id)


# ── slash commands ─────────────────────────────────────────────────────────
async def post_public(interaction: discord.Interaction, embed: discord.Embed):
    if interaction.channel_id == BREAK_CHANNEL_ID:
        await interaction.response.send_message(embed=embed,
                                                allowed_mentions=NO_PINGS)
    else:
        ch = await break_channel()
        if ch:
            await ch.send(embed=embed, allowed_mentions=NO_PINGS)
        await interaction.response.send_message("Done — posted in the breakroom.",
                                                ephemeral=True)


@tree.command(name="break", description="Start a break")
@app_commands.guild_only()
async def cmd_break(interaction: discord.Interaction):
    embed, err = await do_break(interaction.user)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    await post_public(interaction, embed)


@tree.command(name="back", description="End your break")
@app_commands.guild_only()
async def cmd_back(interaction: discord.Interaction):
    embed, err = await do_back(interaction.user)
    if err:
        return await interaction.response.send_message(err, ephemeral=True)
    await post_public(interaction, embed)


@tree.command(name="timeleft",
              description="Check how much break time you have left (does NOT start a break)")
@app_commands.guild_only()
async def cmd_timeleft(interaction: discord.Interaction):
    g, u = interaction.guild_id, interaction.user.id
    active = tracker.active_break(g, u)
    day = active[1] if active else tracker.day_key()
    used = tracker.used_seconds(g, u, day)
    if active:
        state = f"☕ You're **on break** — out for {fmt(time.time() - active[2])}."
    else:
        state = ("🟢 You're **not on break**. "
                 "Type `break` or use `/break` to start one.")
    await interaction.response.send_message(
        f"{state}\nUsed today: {fmt(used)} · Break left: "
        f"{left_text(tracker.limit - used)}", ephemeral=True)


@tree.command(name="breaks", description="Who's on break and today's usage")
@app_commands.guild_only()
async def cmd_breaks(interaction: discord.Interaction):
    rows = tracker.day_summary(interaction.guild_id)
    e = discord.Embed(title=f"☕ Breaks today · {tracker.day_key()}", color=BLUE)
    if not rows:
        e.description = "Nobody has taken a break yet today."
    now = time.time()
    on, off = [], []
    for r in rows:
        m = interaction.guild.get_member(r.user_id)
        name = m.display_name if m else f"<@{r.user_id}>"
        left = tracker.limit - r.used
        flag = " ⚠️" if left < 0 else ""
        if r.on_break_since:
            on.append(f"**{name}** — out {fmt(now - r.on_break_since)} · "
                      f"left {fmt(max(left, 0))}{flag}")
        else:
            off.append(f"**{name}** — used {fmt(r.used)} ({r.breaks}x) · "
                       f"left {fmt(max(left, 0))}{flag}")
    if on:
        e.add_field(name="On break now", value="\n".join(on), inline=False)
    if off:
        e.add_field(name="Back at work", value="\n".join(off)[:1024], inline=False)
    await interaction.response.send_message(embed=e, allowed_mentions=NO_PINGS)


@tree.command(name="endbreak", description="(Managers) End someone's break")
@app_commands.guild_only()
@app_commands.describe(member="Who forgot to say back")
async def cmd_endbreak(interaction: discord.Interaction, member: discord.Member):
    if not is_manager(interaction.user):
        return await interaction.response.send_message(
            "Only managers can do that.", ephemeral=True)
    embed, err = await do_back(member)
    if err:
        return await interaction.response.send_message(
            f"{member.display_name} isn't on break.", ephemeral=True)
    embed.set_author(name=f"Ended by {interaction.user.display_name}")
    await post_public(interaction, embed)


@tree.command(name="logbreak",
              description="(Managers) Add a break someone forgot to record")
@app_commands.guild_only()
@app_commands.describe(member="Who took the break",
                       start="When it started, e.g. 7:32pm",
                       end="When it ended, e.g. 7:57pm (leave empty for now)")
async def cmd_logbreak(interaction: discord.Interaction, member: discord.Member,
                       start: str, end: str | None = None):
    if not is_manager(interaction.user):
        return await interaction.response.send_message(
            "Only managers can do that.", ephemeral=True)
    if tracker.active_break(interaction.guild_id, member.id):
        return await interaction.response.send_message(
            f"{member.display_name} is on break right now — use `/endbreak` "
            f"instead, or wait until they're back.", ephemeral=True)
    try:
        start_ts = tracker.parse_clock(start)
        end_ts = tracker.parse_clock(end) if end else time.time()
        res = tracker.log_break(interaction.guild_id, member.id, start_ts, end_ts)
    except ValueError:
        return await interaction.response.send_message(
            "Couldn't read those times. Use something like `7:32pm` and `7:57pm` "
            "(today, end after start, not in the future).", ephemeral=True)
    span = (f"<t:{int(start_ts)}:t> – <t:{int(end_ts)}:t>")
    e = discord.Embed(
        description=f"📝 Break added for **{member.display_name}** · {span}",
        color=RED if res.over else GREEN)
    e.add_field(name="This break", value=fmt(res.duration))
    e.add_field(name="Break left", value=left_text(res.remaining))
    e.set_author(name=f"Added by {interaction.user.display_name}")
    await post_public(interaction, e)
    await alert_if_over(member.guild, member.id, tracker.day_key(start_ts),
                        still_on_break=False)


@tree.command(name="resetbreak",
              description="(Managers) Give someone their full break back for today")
@app_commands.guild_only()
async def cmd_resetbreak(interaction: discord.Interaction, member: discord.Member):
    if not is_manager(interaction.user):
        return await interaction.response.send_message(
            "Only managers can do that.", ephemeral=True)
    tracker.reset_user_day(interaction.guild_id, member.id)
    await interaction.response.send_message(
        f"Reset — **{member.display_name}** has {LIMIT_MINUTES}:00 again today.",
        allowed_mentions=NO_PINGS)


# ── day-off calendar ───────────────────────────────────────────────────────
def _post_times() -> list[dtime]:
    tz = ZoneInfo(TIMEZONE)
    out = []
    for part in DAYOFF_POST_TIMES.split(","):
        h, _, m = part.strip().partition(":")
        out.append(dtime(int(h), int(m or 0), tzinfo=tz))
    return out


def shift_label(t: dtime) -> str:
    h = t.hour % 12 or 12
    return f"{h}{f':{t.minute:02d}' if t.minute else ''} {'AM' if t.hour < 12 else 'PM'} shift"


def is_qc(member: discord.abc.User) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.manage_guild:
        return True
    return any(r.id == QC_ROLE_ID for r in member.roles)


def dayoff_embed(guild_id: int, footer: str | None = None) -> discord.Embed:
    """Rolling 7-day calendar starting today."""
    start = dayoffs.today()
    end = start + timedelta(days=DAYOFF_DAYS_AHEAD - 1)
    offs = dayoffs.between(guild_id, start, end)
    lines = []
    for i in range(DAYOFF_DAYS_AHEAD):
        d = start + timedelta(days=i)
        tag = " · Today" if i == 0 else " · Tomorrow" if i == 1 else ""
        people = offs.get(d, [])
        who = ", ".join(f"<@{uid}>" + (f" ({note})" if note else "")
                        for uid, note in people) or "-# no one off"
        lines.append(f"**{date_label(d)}**{tag}\n{who}")
    e = discord.Embed(title="📅 Day-offs · next 7 days",
                      description="\n".join(lines), color=BLUE)
    e.set_footer(text=footer or "QC adds day-offs with /dayoff add")
    return e


async def dayoff_channel() -> discord.abc.Messageable | None:
    ch = client.get_channel(DAYOFF_CHANNEL_ID)
    if ch is None:
        try:
            ch = await client.fetch_channel(DAYOFF_CHANNEL_ID)
        except discord.HTTPException:
            log.error("Can't reach day-off channel %s", DAYOFF_CHANNEL_ID)
            return None
    return ch


@tasks.loop(time=_post_times())
async def dayoff_card():
    """Post the 7-day calendar at the start of every shift."""
    ch = await dayoff_channel()
    if not ch or not getattr(ch, "guild", None):
        return
    now = discord.utils.utcnow().astimezone(ZoneInfo(TIMEZONE))
    mins = now.hour * 60 + now.minute

    def gap(t: dtime) -> int:
        d = abs(mins - (t.hour * 60 + t.minute))
        return min(d, 24 * 60 - d)
    slot = min(_post_times(), key=gap)
    key = f"dayoff-card-{now:%Y-%m-%d}-{slot.hour:02d}{slot.minute:02d}"
    if tracker.was_announced(key):      # never double-post the same shift
        return
    try:
        await ch.send(embed=dayoff_embed(ch.guild.id, f"{shift_label(slot)} · "
                                         "QC adds day-offs with /dayoff add"),
                      allowed_mentions=NO_PINGS)
        tracker.mark_announced(key)
    except discord.HTTPException:
        log.exception("Couldn't post day-off card")


dayoff_group = app_commands.Group(name="dayoff", description="Day-off calendar",
                                  guild_only=True)

DATE_HELP = ("Use something like `oct 25`, `10/25`, `2026-10-25`, `sat` or "
             "`tomorrow`.")


def _read_range(start: str, until: str | None):
    today = dayoffs.today()
    s = parse_date(start, today)
    # The end date is read relative to the start, so "dec 30" until "jan 2"
    # lands in the next year.
    e = parse_date(until, s) if until else s
    return s, e, today


@dayoff_group.command(name="add", description="(QC) Schedule a day-off")
@app_commands.describe(member="Who's off",
                       date="First day off, e.g. oct 25, 10/25, sat",
                       until="Last day off, if it's more than one day",
                       note="Optional, e.g. VL, SL, holiday")
async def dayoff_add(interaction: discord.Interaction, member: discord.Member,
                     date: str, until: str | None = None, note: str | None = None):
    if not is_qc(interaction.user):
        return await interaction.response.send_message(
            "Only QC can schedule day-offs.", ephemeral=True)
    try:
        s, e, today = _read_range(date, until)
    except ValueError:
        return await interaction.response.send_message(
            f"Couldn't read that date. {DATE_HELP}", ephemeral=True)
    if s < today:
        return await interaction.response.send_message(
            f"{date_label(s)} is in the past.", ephemeral=True)
    if e < s or (e - s).days + 1 > MAX_RANGE_DAYS:
        return await interaction.response.send_message(
            f"The last day has to be on or after the first, and at most "
            f"{MAX_RANGE_DAYS} days in one go.", ephemeral=True)
    note = (note or "").strip()[:40] or None
    new = dayoffs.add(interaction.guild_id, member.id, s, e, interaction.user.id, note)
    if not new:
        return await interaction.response.send_message(
            f"{member.display_name} is already off on {span_label(s, e)}.",
            ephemeral=True)
    em = discord.Embed(
        description=f"📅 Day-off scheduled for {member.mention} · "
                    f"**{span_label(s, e)}**" + (f" ({note})" if note else ""),
        color=GREEN)
    em.set_author(name=f"Added by {interaction.user.display_name}")
    if (s - today).days < DAYOFF_DAYS_AHEAD:
        em.set_footer(text="Shows on the calendar starting with the next shift card")
    else:
        em.set_footer(text=f"Shows on the calendar from "
                           f"{date_label(s - timedelta(days=DAYOFF_DAYS_AHEAD - 1))}")
    await interaction.response.send_message(embed=em, allowed_mentions=NO_PINGS)


@dayoff_group.command(name="remove", description="(QC) Cancel a scheduled day-off")
@app_commands.describe(member="Whose day-off", date="The day to cancel",
                       until="Last day to cancel, for a range")
async def dayoff_remove(interaction: discord.Interaction, member: discord.Member,
                        date: str, until: str | None = None):
    if not is_qc(interaction.user):
        return await interaction.response.send_message(
            "Only QC can change day-offs.", ephemeral=True)
    try:
        s, e, _ = _read_range(date, until)
        gone = dayoffs.remove(interaction.guild_id, member.id, s, e)
    except ValueError:
        return await interaction.response.send_message(
            f"Couldn't read that date. {DATE_HELP}", ephemeral=True)
    if not gone:
        return await interaction.response.send_message(
            f"{member.display_name} has no day-off on {span_label(s, e)}.",
            ephemeral=True)
    em = discord.Embed(
        description=f"🗑️ Day-off cancelled for {member.mention} · "
                    f"**{', '.join(date_label(d) for d in gone)}**",
        color=ORANGE)
    em.set_author(name=f"Removed by {interaction.user.display_name}")
    await interaction.response.send_message(embed=em, allowed_mentions=NO_PINGS)


@dayoff_group.command(name="week", description="Who's off in the next 7 days")
async def dayoff_week(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=dayoff_embed(interaction.guild_id), ephemeral=True)


@dayoff_group.command(name="post", description="(QC) Post the 7-day day-off card now")
async def dayoff_post(interaction: discord.Interaction):
    if not is_qc(interaction.user):
        return await interaction.response.send_message(
            "Only QC can post the day-off card.", ephemeral=True)
    ch = await dayoff_channel()
    if not ch:
        return await interaction.response.send_message(
            "Couldn't reach the day-off channel.", ephemeral=True)
    em = dayoff_embed(interaction.guild_id,
                      f"Posted by {interaction.user.display_name} · "
                      "QC adds day-offs with /dayoff add")
    if interaction.channel_id == ch.id:
        await interaction.response.send_message(embed=em, allowed_mentions=NO_PINGS)
    else:
        await ch.send(embed=em, allowed_mentions=NO_PINGS)
        await interaction.response.send_message(
            f"Posted in <#{ch.id}>.", ephemeral=True)


@dayoff_group.command(name="list", description="Upcoming day-offs (everyone, or one person)")
@app_commands.describe(member="Only show this person's day-offs")
async def dayoff_list(interaction: discord.Interaction,
                      member: discord.Member | None = None):
    rows = dayoffs.upcoming(interaction.guild_id, dayoffs.today(),
                            member.id if member else None, limit=40)
    title = (f"📅 Upcoming day-offs · {member.display_name}" if member
             else "📅 All upcoming day-offs")
    em = discord.Embed(title=title, color=BLUE)
    if not rows:
        em.description = "Nothing scheduled."
    else:
        em.description = "\n".join(
            f"**{date_label(d)}**" + ("" if member else f" · <@{uid}>")
            + (f" ({note})" if note else "")
            for d, uid, note in rows)[:4000]
    await interaction.response.send_message(embed=em, ephemeral=True)


tree.add_command(dayoff_group)


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
