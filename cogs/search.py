import logging

import discord
from discord import app_commands
from discord.ext import commands

from utils.config import Config
from utils.deal_filter import DealFilter
from utils.embeds import safe_delete
from utils.views import DealPaginator

logger = logging.getLogger("PepperBot.Search")


class SearchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def search_and_paginate(
        self,
        message: discord.Message,
        scraper_method,
        method_args: tuple,
        title_template: str,
        error_msg: str,
    ) -> None:
        """Shared handler for text search commands (search/hot/group)."""
        try:
            result = await scraper_method(*method_args)
            if not result["success"]:
                await message.reply(
                    f"❌ Error: {result.get('error', '?')}", delete_after=10,
                )
                return

            all_deals = result["deals"]
            deals = DealFilter.filter_deals(
                all_deals,
                check_freshness=True,
                check_temperature=True,
                check_price=True,
            )

            if not deals:
                if all_deals:
                    await message.reply(
                        f"🔍 Found {len(all_deals)} deals, none met quality standards.\n"
                        "Filters: Recent (<24h), Hot (≥50°), Valid price",
                        delete_after=20,
                    )
                else:
                    await message.reply(error_msg, delete_after=10)
                return

            view = DealPaginator(deals, message.author)
            await message.reply(
                content=title_template.format(count=len(deals)),
                embed=view.get_initial_embed(),
                view=view,
            )
            await safe_delete(message)

        except Exception as e:
            logger.error("Search error: %s", e, exc_info=True)
            await message.reply(f"⚠️ Error: {e}", delete_after=10)

    # Slash commands

    async def _slash_search(
        self,
        interaction: discord.Interaction,
        result: dict,
        title_ok: str,
        title_empty: str,
    ) -> None:
        if not result["success"]:
            await interaction.followup.send(embed=discord.Embed(
                title="⚠️ Błąd",
                description=f"Błąd pobierania: {result.get('error', '?')}",
                color=Config.COLOR_WARNING,
            ))
            return

        deals = result["deals"]
        if not deals:
            await interaction.followup.send(embed=discord.Embed(
                title="🤷 Brak wyników",
                description=title_empty,
                color=Config.COLOR_NEUTRAL,
            ))
            return

        view = DealPaginator(deals, interaction.user)
        await interaction.followup.send(
            content=f"**{title_ok.format(count=len(deals))}**",
            embed=view.get_initial_embed(),
            view=view,
        )

    @app_commands.command(name="pepper", description="Szukaj okazji na Pepper.pl")
    @app_commands.describe(query="Czego szukasz? (np. lego, rtx 4070)")
    async def search_pepper(
        self, interaction: discord.Interaction, query: str,
    ) -> None:
        await interaction.response.defer()
        result = await self.bot.scraper.search_deals(
            query, limit=Config.DEFAULT_SEARCH_LIMIT,
        )
        await self._slash_search(
            interaction, result,
            title_ok=f"🌶️ Znaleziono {{count}} okazji dla: {query}",
            title_empty=f"Nie znaleziono okazji dla: **{query}**",
        )

    @app_commands.command(
        name="pepperhot", description="Najgorętsze okazje ze strony głównej",
    )
    async def hot_pepper(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        result = await self.bot.scraper.get_hot_deals(
            limit=Config.DEFAULT_SEARCH_LIMIT,
        )
        await self._slash_search(
            interaction, result,
            title_ok="🔥 Top {count} najgorętszych okazji!",
            title_empty="Brak gorących okazji na stronie głównej.",
        )

    @app_commands.command(
        name="pepper_group", description="Pobierz okazje z grupy/kategorii",
    )
    @app_commands.describe(group="Slug grupy (np. elektronika, gry, dom-i-ogrod)")
    async def group_pepper(
        self, interaction: discord.Interaction, group: str,
    ) -> None:
        await interaction.response.defer()
        group = group.lower().strip().replace(" ", "-")
        result = await self.bot.scraper.get_group_deals(
            group, limit=Config.DEFAULT_SEARCH_LIMIT,
        )
        await self._slash_search(
            interaction, result,
            title_ok=f"📂 Top {{count}} okazji z grupy: {group}",
            title_empty=f"Brak okazji w grupie: **{group}**.",
        )

    @app_commands.command(
        name="pepperclean", description="Usuwa ostatnie wiadomości bota",
    )
    @app_commands.describe(limit="Ile wiadomości sprawdzić? (domyślnie 20)")
    @app_commands.guild_only()
    async def clean_pepper(
        self, interaction: discord.Interaction, limit: int = 20,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(
                limit=limit, check=lambda m: m.author == self.bot.user,
            )
            await interaction.followup.send(
                f"🗑️ Usunięto {len(deleted)} moich wiadomości.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Brak uprawnień 'Manage Messages'.", ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Błąd: {e}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SearchCog(bot))
