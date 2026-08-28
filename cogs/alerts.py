import asyncio
import logging
from collections import defaultdict

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.alerts import AlertsManager
from utils.config import Config
from utils.embeds import safe_delete, temperature_icon

logger = logging.getLogger("PepperBot.AlertsCog")


class AlertsCog(commands.Cog):
    pepperwatch_group = app_commands.Group(
        name="pepperwatch",
        description="Zarządzaj powiadomieniami o okazjach",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.alerts_manager = AlertsManager(self.bot.db)
        self.alerts_task.start()

    async def cog_unload(self) -> None:
        self.alerts_task.cancel()

    # --- Task loop ---

    @tasks.loop(minutes=Config.WATCH_INTERVAL_MINUTES)
    async def alerts_task(self) -> None:
        await self._process_alerts()

    @alerts_task.before_loop
    async def before_alerts(self) -> None:
        await self.bot.wait_until_ready()

    async def _process_alerts(self) -> None:
        try:
            notifications = await self.alerts_manager.check_alerts(self.bot.scraper)

            grouped: dict[int, dict[str, list]] = defaultdict(
                lambda: defaultdict(list),
            )
            for n in notifications:
                grouped[n["user_id"]][n["query"]].append(n["deal"])

            for user_id, queries_dict in grouped.items():
                user = self.bot.get_user(user_id)
                if not user:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except (discord.NotFound, Exception) as e:
                        logger.warning("Could not fetch user %d: %s", user_id, e)
                        continue

                for query, deals in queries_dict.items():
                    try:
                        deals.sort(
                            key=lambda d: d.get("temperature", 0), reverse=True,
                        )
                        top = deals[:5]

                        embed = discord.Embed(
                            title=(
                                f"🚨 {len(deals)} "
                                f"{'nowa okazja' if len(deals) == 1 else 'nowych okazji'}"
                                f" dla: {query}"
                            ),
                            color=Config.COLOR_SUCCESS,
                        )
                        for i, deal in enumerate(top, 1):
                            temp = deal.get("temperature", 0)
                            icon = temperature_icon(temp)
                            embed.add_field(
                                name=f"{i}. {deal['title'][:70]}...",
                                value=(
                                    f"💰 **{deal['price']}** | {icon} {temp}°\n"
                                    f"[🔗 Zobacz okazję]({deal['link']})"
                                ),
                                inline=False,
                            )

                        if top[0].get("image_url"):
                            embed.set_thumbnail(url=top[0]["image_url"])
                        embed.set_footer(text="PepperWatch • Sprawdzam co 15 minut")

                        await user.send(embed=embed)
                        await asyncio.sleep(0.5)

                    except discord.Forbidden:
                        logger.warning(
                            "Cannot DM user %s (%d)", user.name, user_id,
                        )
                    except Exception as e:
                        logger.error(
                            "Error sending alert to %d: %s",
                            user_id, e, exc_info=True,
                        )

        except Exception as e:
            logger.error("Alert task error: %s", e, exc_info=True)

    # --- Alerts embed ---

    def _alerts_embed(self, alerts: list[dict]) -> discord.Embed:
        embed = discord.Embed(
            title="🔔 Your Alerts",
            description="Watching these queries:",
            color=Config.COLOR_PRIMARY,
        )
        for i, a in enumerate(alerts, 1):
            price = f"**< {a['max_price']} zł**" if a["max_price"] else "Any price"
            embed.add_field(
                name=f"{i}. {a['query']}", value=f"💰 {price}", inline=False,
            )
        embed.set_footer(text="Use p unwatch:query to remove")
        return embed

    # --- Text command handlers (called by text_commands cog) ---

    async def handle_watch(
        self, message: discord.Message, content: str,
    ) -> None:
        args = content[6:].strip()
        query, max_price = self._parse_watch_args(args)
        if not query:
            await message.reply(
                "❌ Usage: `p watch:query` or `p watch:query < price`",
                delete_after=10,
            )
            return
        _, msg = await self.alerts_manager.add_alert(
            message.author.id, query, max_price,
        )
        await message.reply(msg, delete_after=15)
        await safe_delete(message)

    async def handle_unwatch(
        self, message: discord.Message, content: str,
    ) -> None:
        query = content[8:].strip()
        if not query:
            await message.reply("❌ Usage: `p unwatch:query`", delete_after=10)
            return
        _, msg = await self.alerts_manager.remove_alert(message.author.id, query)
        await message.reply(msg, delete_after=10)
        await safe_delete(message)

    async def handle_list(self, message: discord.Message) -> None:
        alerts = await self.alerts_manager.get_alerts(message.author.id)
        if not alerts:
            await message.reply("🔭 No active alerts.", delete_after=10)
            await safe_delete(message)
            return
        await message.reply(embed=self._alerts_embed(alerts))
        await safe_delete(message)

    @staticmethod
    def _parse_watch_args(text: str) -> tuple[str, float | None]:
        """Parse 'query < price' syntax."""
        if "<" in text:
            parts = text.split("<", 1)
            query = parts[0].strip()
            try:
                return query, float(parts[1].strip())
            except (ValueError, IndexError):
                return query, None
        return text.strip(), None

    # --- Slash commands ---

    @pepperwatch_group.command(name="add", description="Dodaj powiadomienie")
    @app_commands.describe(
        query="Fraza do wyszukania",
        max_price="Maksymalna cena (opcjonalnie)",
    )
    async def pw_add(
        self,
        interaction: discord.Interaction,
        query: str,
        max_price: float | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        _, msg = await self.alerts_manager.add_alert(
            interaction.user.id, query, max_price,
        )
        await interaction.followup.send(msg, ephemeral=True)

    @pepperwatch_group.command(
        name="list", description="Pokaż moje aktywne powiadomienia",
    )
    async def pw_list(self, interaction: discord.Interaction) -> None:
        alerts = await self.alerts_manager.get_alerts(interaction.user.id)
        if not alerts:
            await interaction.response.send_message(
                "🔭 Brak aktywnych powiadomień.", ephemeral=True,
            )
            return
        embed = self._alerts_embed(alerts)
        embed.set_footer(text="Użyj /pepperwatch remove [fraza] aby usunąć.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @pepperwatch_group.command(name="remove", description="Usuń powiadomienie")
    @app_commands.describe(query="Fraza do usunięcia")
    async def pw_remove(
        self, interaction: discord.Interaction, query: str,
    ) -> None:
        _, msg = await self.alerts_manager.remove_alert(
            interaction.user.id, query,
        )
        msg = msg.replace("p alerts", "/pepperwatch list")
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AlertsCog(bot))
