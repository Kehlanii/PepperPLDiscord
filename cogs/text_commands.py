import logging

import discord
from discord.ext import commands

from utils.config import Config
from utils.embeds import safe_delete

logger = logging.getLogger("PepperBot.TextCommands")


class TextCommandsCog(commands.Cog):
    """Routes 'p ' prefix text commands to domain cogs."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.content.startswith("p "):
            return

        content = message.content[2:].strip()
        if not content:
            return

        try:
            # Clean (admin util)
            if content.startswith("clean"):
                await self._handle_clean(message, content)
                return

            # Alerts
            alerts = self.bot.get_cog("AlertsCog")
            if alerts:
                if content.startswith("watch:"):
                    await alerts.handle_watch(message, content)
                    return
                if content.startswith("unwatch:"):
                    await alerts.handle_unwatch(message, content)
                    return
                if content in ("alerts", "list"):
                    await alerts.handle_list(message)
                    return

            # Flights
            flights = self.bot.get_cog("FlightsCog")
            if content == "fly" and flights:
                await flights.handle_fly(message)
                return

            # Categories
            categories = self.bot.get_cog("CategoriesCog")
            if content.startswith("cat ") and categories:
                await categories.handle_cat_command(message, content)
                return

            # Search variants (hot, group, preview, fallback)
            search = self.bot.get_cog("SearchCog")
            if search:
                if content == "hot":
                    await search.search_and_paginate(
                        message,
                        self.bot.scraper.get_hot_deals,
                        (Config.DEFAULT_SEARCH_LIMIT,),
                        "**🔥 Top {count} hot deals!**",
                        "🤷 No hot deals found.",
                    )
                    return

                if content.startswith("group:"):
                    slug = content[6:].strip().lower().replace(" ", "-")
                    if slug:
                        await search.search_and_paginate(
                            message,
                            self.bot.scraper.get_group_deals,
                            (slug, Config.DEFAULT_SEARCH_LIMIT),
                            f"**📂 Top {{count}} deals from: {slug}**",
                            f"🤷 No deals in: **{slug}**",
                        )
                        return

                if content.startswith("preview:"):
                    slug = content[8:].strip().lower()
                    if slug and categories:
                        await categories.handle_preview(message, slug)
                        return

                # Fallback: treat as search query
                await search.search_and_paginate(
                    message,
                    self.bot.scraper.search_deals,
                    (content, Config.DEFAULT_SEARCH_LIMIT),
                    f"**🌶️ Found {{count}} deals for: {content}**",
                    f"🤷 No deals for: **{content}**",
                )

        except Exception as e:
            logger.error("Text command error: %s", e, exc_info=True)
            try:
                await message.reply(f"⚠️ Error: {e}", delete_after=10)
            except Exception as e_inner:
                logger.debug("Reply failed: %s", e_inner)

    async def _handle_clean(
        self, message: discord.Message, content: str,
    ) -> None:
        if not message.channel.permissions_for(message.guild.me).manage_messages:
            await message.reply(
                "❌ Missing 'Manage Messages' permission.", delete_after=10,
            )
            return
        parts = content.split()
        limit = int(parts[1]) if len(parts) > 1 else 20
        deleted = await message.channel.purge(
            limit=limit, check=lambda m: m.author == self.bot.user,
        )
        await message.reply(f"🗑️ Deleted {len(deleted)} messages", delete_after=5)
        await safe_delete(message)



async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TextCommandsCog(bot))
