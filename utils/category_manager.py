import datetime
import logging
import re
from typing import Any

import discord

from .scraper import PepperScraper

logger = logging.getLogger("PepperBot.CategoryManager")

_EMOJI_MAP = {
    "bilety-lotnicze": "✈️",
    "podzespoly-komputerowe": "💻",
    "smartfony": "📱",
    "gry": "🎮",
    "lego": "🧱",
    "laptopy": "💻",
    "dom-i-ogrod": "🏡",
    "narzedzia": "🔧",
    "elektronika": "⚡",
    "konsole": "🎮",
    "moda-i-akcesoria": "👔",
    "zabawki": "🧸",
    "sport-i-wypoczynek": "⚽",
    "ksiazki": "📚",
    "zdrowie-i-uroda": "💄",
    "jedzenie-i-napoje": "🍕",
    "dom-i-meble": "🛋️",
    "tv-audio-foto": "📺",
    "auto-moto": "🚗",
}

_VALID_DAYS = (
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
)
_DAY_TO_WEEKDAY = {d: i for i, d in enumerate(_VALID_DAYS)}
_VALID_FREQUENCIES = ("daily", "weekly", "biweekly", "monthly")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def get_category_emoji(slug: str) -> str:
    return _EMOJI_MAP.get(slug, "📂")


def format_schedule(cat: dict[str, Any]) -> str:
    # Handles both DB rows (schedule_type) and temp dicts (type)
    stype = cat.get("schedule_type") or cat.get("type", "")
    time = cat.get("schedule_time") or cat.get("time", "??:??")
    day = cat.get("schedule_day") or cat.get("day")
    date = cat.get("schedule_date") or cat.get("date")

    if stype == "daily":
        return f"Daily at {time}"
    if stype == "weekly":
        return f"Weekly ({(day or '?').capitalize()}) at {time}"
    if stype == "biweekly":
        return f"Biweekly ({(day or '?').capitalize()}) at {time}"
    if stype == "monthly":
        return f"Monthly (day {date or '?'}) at {time}"
    return "Unknown schedule"


async def validate_slug(
    scraper: PepperScraper, slug: str,
) -> tuple[bool, str | None]:
    if not _SLUG_RE.match(slug) or len(slug) > 50:
        return False, "Invalid slug. Lowercase letters, numbers, hyphens only (max 50)."
    result = await scraper.get_group_deals(slug, limit=1)
    if result["success"] and result["deals"]:
        return True, None
    return False, f"Category '{slug}' not found on Pepper.pl"


async def validate_channel(
    bot: discord.Client, channel: discord.TextChannel,
) -> tuple[bool, str | None]:
    perms = channel.permissions_for(channel.guild.me)
    if not perms.send_messages:
        return False, f"Missing 'Send Messages' in {channel.mention}"
    if not perms.embed_links:
        return False, f"Missing 'Embed Links' in {channel.mention}"
    return True, None


def parse_schedule(
    frequency: str, time: str, day: str | None = None, date: int | None = None,
) -> tuple[bool, dict | None, str | None]:
    if not _TIME_RE.match(time):
        return False, None, "Time must be HH:MM format (e.g., 09:00)"

    freq = frequency.lower()
    if freq not in _VALID_FREQUENCIES:
        return False, None, f"Frequency must be one of: {', '.join(_VALID_FREQUENCIES)}"

    if freq in ("weekly", "biweekly") and not day:
        return False, None, f"{freq} requires a day (e.g., monday)"

    if freq == "monthly":
        if not date:
            return False, None, "Monthly requires a date (1-31)"
        if not 1 <= date <= 31:
            return False, None, "Date must be 1-31"

    if day and day.lower() not in _VALID_DAYS:
        return False, None, f"Day must be one of: {', '.join(_VALID_DAYS)}"

    schedule = {
        "type": freq,
        "time": time,
        "day": day.lower() if day else None,
        "date": date,
    }
    return True, schedule, None


def should_run_now(category: dict[str, Any]) -> bool:
    """2-minute window match + 30-minute cooldown to prevent duplicate runs."""
    now = datetime.datetime.now()

    h, m = map(int, category["schedule_time"].split(":"))
    scheduled = now.replace(hour=h, minute=m, second=0, microsecond=0)

    if abs((now - scheduled).total_seconds()) >= 120:
        return False

    # Cooldown: skip if ran within last 30 min
    if last := category.get("last_run"):
        try:
            if (now - datetime.datetime.fromisoformat(last)).total_seconds() < 1800:
                return False
        except (ValueError, TypeError):
            pass

    stype = category["schedule_type"]
    if stype == "daily":
        return True

    if stype in ("weekly", "biweekly"):
        target = _DAY_TO_WEEKDAY.get(category.get("schedule_day"))
        if target is None or now.weekday() != target:
            return False
        if stype == "biweekly" and (last_run := category.get("last_run")):
            try:
                if (now - datetime.datetime.fromisoformat(last_run)).days < 13:
                    return False
            except (ValueError, TypeError):
                pass
        return True

    if stype == "monthly":
        return now.day == category.get("schedule_date")

    return False
