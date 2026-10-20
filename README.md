# Breakroom Bot

Everyone gets **30 minutes of break per day** (resets at midnight Manila time),
usable in as many chunks as they want. Every break is announced in the breakroom.

| Someone types | Bot posts |
|---|---|
| `break` | ☕ **Blank** went on break — Break left: **23:15** minutes |
| `back` | ✅ **Blank** is back — This break: 06:45 · Break left: **16:30** minutes |
| `back` (over 30 min) | Red message: **01:45 over** the limit + pings the manager role |

With **1 minute left**, the bot pings the person in the breakroom with a live
countdown ("time's up in 59 seconds"). It flips to "✅ Back with 00:23 to spare"
or "⏰ Time's up" when the break ends. Change the warning time with the
`REMINDER_SECONDS` variable (default `60`, `0` turns it off).

If someone is **still on break** when their 30 minutes run out, the bot pings
the managers right away (once per person per day) — they don't have to wait for
the person to come back.

`brb` / `I'm back` also work. Slash commands:

- `/break`, `/back`: same as typing it
- `/timeleft`: your time left, only you see it (does not start a break)
- `/breaks`: who's out right now + everyone's usage today
- `/endbreak @person` (managers): ends a break someone forgot to close
- `/logbreak @person 7:32pm 7:57pm` (managers): adds a break someone forgot to record
- `/resetbreak @person` (managers): gives them a full 30 min again today

"Managers" = anyone with the manager role or *Manage Server* permission.

### Day-off calendar

At the start of every shift (**8 AM, 4 PM, 12 AM** Manila time) the bot posts
who's off for the **next 7 days**, so a scheduled day-off appears a week ahead.

- `/dayoff add @person oct 25` (QC): schedule a day-off. Optional `until:` for a
  range (up to 31 days) and `note:` (VL, SL, holiday…). Dates can be typed as
  `oct 25`, `10/25`, `2026-10-25`, `sat` or `tomorrow`.
- `/dayoff remove @person oct 25` (QC): cancel it (also takes `until:`)
- `/dayoff post` (QC): post the 7-day card in the channel right now
- `/dayoff week`: the 7-day calendar, only you see it
- `/dayoff list [@person]`: all upcoming day-offs, only you see it

"QC" = anyone with the QC role (`QC_ROLE_ID`) or *Manage Server* permission.
Day-offs are stored in the same database on the `/data` volume.

---

## Setup (about 15 minutes)

### 1. Create the bot in Discord
1. Go to <https://discord.com/developers/applications> → **New Application** → name it (e.g. *Break Tracker*).
2. **Bot** tab → **Reset Token** → copy the token somewhere safe (you'll paste it into Railway). Never post it in Discord.
3. Same page, under *Privileged Gateway Intents*, turn on **Message Content Intent** → Save.
4. **OAuth2 → URL Generator**: tick scopes `bot` and `applications.commands`; bot permissions
   `View Channels`, `Send Messages`, `Embed Links`, `Read Message History`, `Mention @everyone, @here, and All Roles`.
   Open the generated URL and add the bot to your **work** server.

### 2. Get the IDs
In Discord: **User Settings → Advanced → Developer Mode** on. Then:
- Right-click the **breakroom channel** → *Copy Channel ID*
- Server Settings → Roles → right-click the **manager role** → *Copy Role ID*

### 3. Deploy on Railway
1. Put these files in a new GitHub repo (github.com → New repository → *uploading an existing file* → drag everything in).
2. <https://railway.com> → **New Project → Deploy from GitHub repo** → pick the repo.
3. Service → **Variables** → add:

   | Name | Value |
   |---|---|
   | `DISCORD_TOKEN` | the bot token from step 1 |
   | `BREAK_CHANNEL_ID` | breakroom channel ID |
   | `MANAGER_ROLE_ID` | manager role ID |
   | `BREAK_LIMIT_MINUTES` | `30` (optional) |
   | `TIMEZONE` | `Asia/Manila` (optional) |
   | `QC_ROLE_ID` | QC role ID (optional, defaults to the current QC role) |
   | `DAYOFF_CHANNEL_ID` | where the 7-day card posts (optional, defaults to the breakroom) |
   | `DAYOFF_POST_TIMES` | `08:00,16:00,00:00` (optional, shift start times) |

4. **Add a volume** (right-click the service → *Attach Volume*), mount path **`/data`**.
   Without it, break history is wiped every time Railway redeploys.
5. Deploy. The logs should say `Logged in as Break Tracker…`. Type `break` in the breakroom to test.

### Update announcements
Every update gets a post in the breakroom. To announce one, add an entry at the
**top** of `UPDATES` in `changelog.py` (with a new unique `id`) and deploy.
The bot posts it once when it starts up, and never reposts it on restarts.

### Notes
- A break counts toward the day it **started** (a break from 11:50 PM to 12:10 AM counts for the earlier day).
- Railway's Trial is a one-time $5 credit and the Free plan gives $1/month; a
  small bot like this fits comfortably in **Hobby ($5/month)**. Check
  <https://docs.railway.com/reference/pricing> for current numbers.
- Run tests locally: `pip install pytest discord.py tzdata && pytest`
