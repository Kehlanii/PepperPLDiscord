import logging

import discord

logger = logging.getLogger("PepperBot.Embeds")


def temperature_icon(temp: int) -> str:
    if temp >= 500:
        return "🌋"
    if temp >= 100:
        return "🔥"
    if temp > 0:
        return "👍"
    return "❄️"


async def safe_delete(message: discord.Message) -> None:
    """Best-effort message deletion. Silently eats permission/404 errors."""
    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    except Exception as e:
        logger.debug("Could not delete message: %s", e)
