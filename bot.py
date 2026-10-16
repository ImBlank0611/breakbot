"""Breakroom bot — 30 minutes of break per person per day, announced publicly.

Type `break` / `back` in the breakroom channel (or use /break and /back).
Managers get pinged when someone runs past the daily limit.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import tasks

from tracker import BreakTracker, fmt

# ── configuration (set these as Railway variables) ─────────────────────────
TOKEN = os.environ["DISCORD_TOKEN"]
BREAK_CHANNEL_ID = int(os.environ["BREAK_CHANNEL_ID"])
MANAGER_ROLE_ID = int(os.environ.get("MANAGER_ROLE_ID", "0") or 0)
LIMIT_MINUTES = int(os.environ.get("BREAK_LIMIT_MINUTES", "30"))
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Manila")
DATA_DIR = os.environ.get("DATA_DIR", "/data")

BREAK_WORDS = {"break", "brb", "on break", "going on break"}
BACK_WORDS = {"back", "im back", "i'm back", "i’m back", "back from break"}

YELLOW, GREEN, RED, BLUE = 0xF1C40F, 0x2ECC71, 0xE74C3C, 0x5865F2

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
        return None, (f"You're not on break. "
                      f"Break left today: {left_text(res.remaining)}.")
    day = active[1]
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
    log.info("Logged in as %s — watching channel %s, limit %s min, tz %s",
             client.user, BREAK_CHANNEL_ID, LIMIT_MINUTES, TIMEZONE)


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


@tasks.loop(seconds=30)
async def watch_breaks():
    """Catch people who are still on break when they pass the limit."""
    for guild_id, user_id, day, _start in tracker.open_breaks():
        guild = client.get_guild(guild_id)
        if guild:
            await alert_if_over(guild, user_id, day, still_on_break=True)


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


@tree.command(name="mybreak", description="How much break you have left today")
@app_commands.guild_only()
async def cmd_mybreak(interaction: discord.Interaction):
    g, u = interaction.guild_id, interaction.user.id
    active = tracker.active_break(g, u)
    day = active[1] if active else tracker.day_key()
    used = tracker.used_seconds(g, u, day)
    state = (f"On break for {fmt(time.time() - active[2])}. " if active else "")
    await interaction.response.send_message(
        f"{state}Used today: {fmt(used)} · Break left: "
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


if __name__ == "__main__":
    client.run(TOKEN, log_handler=None)
