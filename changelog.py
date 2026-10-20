"""Update announcements.

When a new entry is added at the TOP of this list and the bot is redeployed,
it posts the entry in the breakroom once. Already-announced entries are never
reposted (tracked in the database), so restarts don't spam the channel.

Each entry needs a unique `id` — use the date plus a short name.
"""

UPDATES = [
    {
        "id": "2026-10-20-dayoff-calendar",
        "title": "📅 Break Bot Update: day-off calendar",
        "body": (
            "The bot now keeps a **day-off calendar**. At the start of every "
            "shift (**8 AM, 4 PM and 12 AM**) it posts who's off for the "
            "**next 7 days**, so a scheduled day-off shows up a week ahead.\n\n"
            "**QC:** schedule one with `/dayoff add` — e.g. "
            "`/dayoff add @Ben oct 23 until: oct 24 note: VL`. "
            "Cancel with `/dayoff remove`.\n"
            "**Everyone:** `/dayoff week` shows the 7-day calendar and "
            "`/dayoff list` shows upcoming day-offs (only you see these)."
        ),
    },
    {
        "id": "2026-10-17-timeleft-logbreak",
        "title": "🔧 Break Bot Update: clearer commands",
        "body": (
            "**`/mybreak` is now `/timeleft`.** It only *checks* your time — it "
            "does **not** start a break.\n\n"
            "**To start a break:** type `break` (or use `/break`)\n"
            "**When you're back:** type `back` (or use `/back`)\n\n"
            "Forgot to type `break` before leaving? Tell a manager. They can now "
            "add a missed break with `/logbreak`, so your time stays accurate."
        ),
    },
    {
        "id": "2026-10-17-one-minute-warning",
        "title": "⏰ Break Bot Update: 1-minute warning",
        "body": (
            "When you have **1 minute of break left**, the bot will ping you here "
            "with a live countdown:\n"
            "> ⏰ **@You**, 1 minute of break left — time's up **in 59 seconds**\n\n"
            "• **Back in time:** the reminder changes to ✅ *Back with 00:23 to spare*\n"
            "• **Time runs out:** it changes to ⏰ *Time's up* and managers are notified\n\n"
            "**How it works:**\n"
            "• You get **30 minutes** of break per day, split however you like\n"
            "• Type `break` when you leave and `back` when you return\n"
            "• `/mybreak` shows how much break you have left (only you can see it)\n"
            "• Break time resets at **midnight**\n\n"
            "Keep an eye out for the ping so you're back on time! 🙌"
        ),
    },
]
