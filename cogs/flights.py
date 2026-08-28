import datetime
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.config import Config
from utils.embeds import safe_delete, temperature_icon

if TYPE_CHECKING:
    from bot import PepperBot

logger = logging.getLogger("PepperBot.Flights")

MAX_FLIGHT_DEALS = 10


class FlightsCog(commands.Cog):
    def __init__(self, bot: "PepperBot"):
        self.bot = bot
        self.flight_task.start()

    async def cog_unload(self) -> None:
        self.flight_task.cancel()

    @tasks.loop(time=datetime.time(hour=Config.FLIGHT_SCHEDULE_HOUR, minute=0))
    async def flight_task(self) -> None:
        await self._process_flights()

    @flight_task.before_loop
    async def before_flight(self) -> None:
        await self.bot.wait_until_ready()

    async def _process_flights(
        self,
        *,
        manual: bool = False,
        interaction: discord.Interaction | None = None,
    ) -> None:
        channel = (
            interaction.channel if interaction
            else self.bot.get_channel(Config.FLIGHT_CHANNEL_ID)
        )
        if not channel:
            logger.warning("Flight channel %d not found", Config.FLIGHT_CHANNEL_ID)
            if interaction:
                await interaction.followup.send(
                    "⚠️ Flight channel not found.", ephemeral=True,
                )
            return

        try:
            result = await self.bot.scraper.get_flight_deals(limit=20)
            if not result["success"]:
                if interaction:
                    await interaction.followup.send(
                        f"❌ Błąd: {result.get('error')}", ephemeral=True,
                    )
                return

            deals = result["deals"]
            if not deals:
                if interaction:
                    await interaction.followup.send(
                        "🤷 Brak okazji lotniczych.", ephemeral=True,
                    )
                return

            # Deduplicate against sent history
            new_deals = []
            for deal in deals:
                deal_id = deal["link"]
                is_sent = await self.bot.db.is_deal_sent(deal_id)
                if manual or not is_sent:
                    new_deals.append(deal)
                    if not manual:
                        await self.bot.db.add_sent_deal(deal_id)

            if not new_deals:
                logger.info("No new flight deals")
                if interaction:
                    await interaction.followup.send(
                        "Brak nowych okazji.", ephemeral=True,
                    )
                return

            new_deals.sort(
                key=lambda x: x.get("temperature", 0), reverse=True,
            )
            top = new_deals[:MAX_FLIGHT_DEALS]

            embed = discord.Embed(
                title=f"✈️ Dzienny Raport Lotniczy - {datetime.date.today()}",
                description=f"Znaleziono **{len(new_deals)}** okazji. Najlepsze:",
                color=Config.COLOR_PRIMARY,
            )
            for i, deal in enumerate(top, 1):
                price = deal.get("price") or "???"
                temp = deal.get("temperature", 0)
                merchant = deal.get("merchant", "Unknown")
                icon = temperature_icon(temp)
                embed.add_field(
                    name=f"{i}. {deal['title'][:80]}...",
                    value=(
                        f"💰 **{price}** | {icon} {temp}° | 🏪 {merchant}\n"
                        f"[🔗 Zobacz okazję]({deal['link']})"
                    ),
                    inline=False,
                )

            embed.set_footer(text="Pepper.pl Bot • Aktualizacja codzienna o 08:00")
            if top[0].get("image_url"):
                embed.set_thumbnail(url=top[0]["image_url"])

            await channel.send(embed=embed)

            if not manual:
                logger.info("Sent flight digest: %d deals", len(top))
            elif interaction:
                await interaction.followup.send(
                    "✅ Wysłano raport lotniczy.", ephemeral=True,
                )

        except Exception as e:
            logger.error("Flight task error: %s", e, exc_info=True)
            if interaction:
                await interaction.followup.send(
                    f"⚠️ Błąd: {e}", ephemeral=True,
                )

    # --- Text command handler ---

    async def handle_fly(self, message: discord.Message) -> None:
        if not message.author.guild_permissions.administrator:
            await message.reply("❌ Admin only.", delete_after=10)
            return
        await message.reply("⚡ Triggering flight deals...", delete_after=5)
        await self._process_flights(manual=True)
        await safe_delete(message)

    # --- Slash command ---

    @app_commands.command(
        name="flynow",
        description="[Admin] Ręczne wywołanie raportu lotniczego",
    )
    async def fly_now(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._process_flights(manual=True, interaction=interaction)


async def setup(bot: "PepperBot") -> None:
    await bot.add_cog(FlightsCog(bot))
