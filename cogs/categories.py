import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import category_manager as catmgr
from utils.config import Config
from utils.embeds import safe_delete, temperature_icon
from utils.pricing import parse_price

logger = logging.getLogger("PepperBot.Categories")


class CategoriesCog(commands.Cog):
    category_group = app_commands.Group(
        name="category",
        description="Manage automated category notifications",
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.category_task.start()
        self.cleanup_task.start()

    async def cog_unload(self) -> None:
        self.category_task.cancel()
        self.cleanup_task.cancel()

    # --- Task loops ---

    @tasks.loop(minutes=1)
    async def category_task(self) -> None:
        try:
            categories = await self.bot.db.get_active_categories()
            if not categories:
                return

            to_run = [c for c in categories if catmgr.should_run_now(c)]
            if not to_run:
                return

            logger.info("Processing %d scheduled categories", len(to_run))
            for i, cat in enumerate(to_run):
                try:
                    if i > 0:
                        await asyncio.sleep(Config.CATEGORY_STAGGER_DELAY)
                    await self._process_notification(cat)
                except Exception as e:
                    logger.error(
                        "Category %s error: %s", cat["slug"], e, exc_info=True,
                    )
                    await self.bot.db.update_category_stats(
                        cat["id"], 0, 0, errors=1,
                    )

        except Exception as e:
            logger.error("Category task error: %s", e, exc_info=True)

    @category_task.before_loop
    async def before_category(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=Config.CLEANUP_INTERVAL_HOURS)
    async def cleanup_task(self) -> None:
        try:
            d1 = await self.bot.db.cleanup_old_deals(days=Config.CLEANUP_DAYS_OLD)
            d2 = await self.bot.db.cleanup_category_deals(
                days=Config.CLEANUP_DAYS_OLD,
            )
            logger.info(
                "Cleanup: %d flight deals, %d category deals removed", d1, d2,
            )
        except Exception as e:
            logger.error("Cleanup error: %s", e, exc_info=True)

    @cleanup_task.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()

    # --- Core notification logic ---

    async def _process_notification(
        self,
        category: dict[str, Any],
        *,
        manual: bool = False,
        interaction: discord.Interaction | None = None,
    ) -> None:
        channel = self.bot.get_channel(category["channel_id"])
        if not channel:
            logger.warning(
                "Channel %d missing for %s",
                category["channel_id"], category["slug"],
            )
            if not manual:
                await self.bot.db.update_category_status(
                    category["guild_id"], category["slug"], "disabled",
                )
            return

        result = await self.bot.scraper.get_group_deals(category["slug"], limit=20)
        if not result["success"]:
            logger.error(
                "Scrape failed for %s: %s",
                category["slug"], result.get("error"),
            )
            if interaction:
                await interaction.followup.send(
                    "❌ Failed to fetch deals.", ephemeral=True,
                )
            await self.bot.db.update_category_stats(
                category["id"], 0, 0, errors=1,
            )
            return

        deals = result["deals"]
        if not deals:
            if interaction:
                await interaction.followup.send(
                    f"🤷 No deals for **{category['slug']}**", ephemeral=True,
                )
            await self.bot.db.update_category_stats(category["id"], 0, 0)
            return

        # Filter + deduplicate
        new_deals: list[dict] = []
        to_mark: list[tuple] = []

        for deal in deals:
            deal_id = deal["link"]

            if category.get("min_temperature", 0) > 0:
                if deal.get("temperature", 0) < category["min_temperature"]:
                    continue

            if category.get("max_price"):
                dp = parse_price(deal.get("price"))
                if dp and dp > 0 and dp > category["max_price"]:
                    continue

            is_sent = await self.bot.db.is_category_deal_sent(
                category["id"], deal_id,
            )
            if manual or not is_sent:
                new_deals.append(deal)
                if not manual:
                    to_mark.append((category["id"], deal_id))

        if to_mark:
            await self.bot.db.mark_category_deals_sent_batch(to_mark)

        if not new_deals:
            if interaction:
                await interaction.followup.send(
                    f"No new deals since last check for **{category['slug']}**",
                    ephemeral=True,
                )
            await self.bot.db.update_category_stats(
                category["id"], len(deals), 0,
            )
            return

        new_deals.sort(key=lambda x: x.get("temperature", 0), reverse=True)
        top = new_deals[: Config.MAX_DEALS_PER_NOTIFICATION]
        emoji = catmgr.get_category_emoji(category["slug"])

        embed = discord.Embed(
            title=f"{emoji} {category.get('name') or category['slug']}",
            description=f"Found **{len(new_deals)}** new deals. Hottest:",
            color=Config.COLOR_PRIMARY,
        )
        for i, deal in enumerate(top, 1):
            temp = deal.get("temperature", 0)
            icon = temperature_icon(temp)
            price = deal.get("price") or "???"
            merchant = deal.get("merchant", "Unknown")
            embed.add_field(
                name=f"{i}. {deal['title'][:80]}...",
                value=(
                    f"💰 **{price}** | {icon} {temp}° | 🪐 {merchant}\n"
                    f"[🔗 View deal]({deal['link']})"
                ),
                inline=False,
            )

        embed.set_footer(
            text=f"Pepper.pl • {catmgr.format_schedule(category)}",
        )
        if top[0].get("image_url"):
            embed.set_thumbnail(url=top[0]["image_url"])

        await channel.send(embed=embed)
        await self.bot.db.update_category_last_run(category["id"])
        await self.bot.db.update_category_stats(
            category["id"], len(deals), len(new_deals),
        )

        if not manual:
            logger.info(
                "Sent %d deals for category %s", len(top), category["slug"],
            )
        elif interaction:
            await interaction.followup.send(
                f"✅ Sent {len(top)} deals to {channel.mention}", ephemeral=True,
            )

    # --- Validation + creation ---

    async def _validate_and_create(
        self,
        guild_id: int,
        slug: str,
        channel: discord.TextChannel,
        frequency: str,
        time: str,
        day: str | None,
        date: int | None,
        min_temp: int,
        max_price: float | None,
    ) -> tuple[bool, str | None, int | None]:
        slug = slug.lower().strip()

        if await self.bot.db.get_category_by_slug(guild_id, slug):
            return False, f"⚠️ Category **{slug}** already exists.", None

        cats = await self.bot.db.get_guild_categories(guild_id)
        if len(cats) >= Config.MAX_CATEGORIES_PER_GUILD:
            return (
                False,
                f"❌ Max {Config.MAX_CATEGORIES_PER_GUILD} categories per server.",
                None,
            )

        ok, err = await catmgr.validate_slug(self.bot.scraper, slug)
        if not ok:
            return False, err, None

        ok, err = await catmgr.validate_channel(self.bot, channel)
        if not ok:
            return False, err, None

        ok, schedule, err = catmgr.parse_schedule(frequency, time, day, date)
        if not ok:
            return False, err, None

        cat_id = await self.bot.db.add_category_config(
            guild_id=guild_id,
            slug=slug,
            channel_id=channel.id,
            schedule_type=schedule["type"],
            schedule_time=schedule["time"],
            schedule_day=schedule["day"],
            schedule_date=schedule["date"],
            min_temperature=min_temp,
            max_price=max_price,
        )
        if not cat_id:
            return False, "❌ Database error.", None

        return True, None, cat_id

    # --- Category list embed ---

    def _category_list_embed(self, categories: list[dict]) -> discord.Embed:
        embed = discord.Embed(
            title="📋 Active Categories",
            description=f"Managing {len(categories)} automated notifications",
            color=Config.COLOR_PRIMARY,
        )
        for i, cat in enumerate(categories, 1):
            emoji = catmgr.get_category_emoji(cat["slug"])
            filters = []
            if cat.get("min_temperature", 0) > 0:
                filters.append(f"🌡️ Min: {cat['min_temperature']}°")
            if cat.get("max_price"):
                filters.append(f"💰 Max: {cat['max_price']} zł")
            filter_str = " | ".join(filters) or "No filters"
            sched = catmgr.format_schedule(cat)
            icon = "✅" if cat["status"] == "active" else "⏸️"
            embed.add_field(
                name=f"{i}. {emoji} {cat['slug']}",
                value=f"{icon} {sched}\n📍 <#{cat['channel_id']}>\n{filter_str}",
                inline=False,
            )
        return embed

    # --- Text command handlers ---

    async def handle_cat_command(
        self, message: discord.Message, content: str,
    ) -> None:
        if not message.author.guild_permissions.administrator:
            await message.reply("❌ Admin only.", delete_after=10)
            return

        sub = content[4:].strip()
        try:
            if sub == "list":
                await self._text_list(message)
            elif sub.startswith("add:"):
                await self._text_add(message, sub[4:])
            elif sub.startswith("rm:"):
                await self._text_remove(message, sub[3:])
            elif sub.startswith("pause:"):
                await self._text_status(message, sub[6:], "paused")
            elif sub.startswith("resume:"):
                await self._text_status(message, sub[7:], "active")
            elif sub.startswith("run:"):
                await self._text_trigger(message, sub[4:])
            else:
                await message.reply(
                    "❌ Usage: `p cat [list|add:|rm:|pause:|resume:|run:]`",
                    delete_after=10,
                )
        except Exception as e:
            logger.error("Category cmd error: %s", e, exc_info=True)
            await message.reply(f"⚠️ Error: {e}", delete_after=10)

    async def _text_list(self, message: discord.Message) -> None:
        cats = await self.bot.db.get_guild_categories(message.guild.id)
        if not cats:
            await message.reply(
                "📭 No categories. Use `p cat add:slug ...`", delete_after=10,
            )
            await safe_delete(message)
            return
        await message.reply(embed=self._category_list_embed(cats))
        await safe_delete(message)

    async def _text_add(self, message: discord.Message, args: str) -> None:
        parts = args.split()
        if len(parts) < 4:
            await message.reply(
                "❌ `p cat add:slug frequency time #channel [day] [min:N] [max:N]`",
                delete_after=15,
            )
            return

        slug = parts[0].lower().strip()
        frequency = parts[1].lower()
        time = parts[2]

        channel = None
        for word in parts:
            if word.startswith("<#") and word.endswith(">"):
                try:
                    channel = self.bot.get_channel(int(word[2:-1]))
                except ValueError:
                    pass
                break

        if not channel:
            await message.reply(
                "❌ Channel not found. Use #channel mention.", delete_after=10,
            )
            return

        day, date, min_temp, max_price = None, None, 0, None
        for part in parts:
            if part.startswith("min:"):
                try:
                    min_temp = int(part[4:])
                except ValueError:
                    pass
            elif part.startswith("max:"):
                try:
                    max_price = float(part[4:])
                except ValueError:
                    pass
            elif part.lower() in (
                "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday",
            ):
                day = part.lower()
            elif (
                part.isdigit()
                and frequency == "monthly"
                and 1 <= int(part) <= 31
            ):
                date = int(part)

        ok, err, _ = await self._validate_and_create(
            message.guild.id, slug, channel,
            frequency, time, day, date, min_temp, max_price,
        )
        if not ok:
            await message.reply(err, delete_after=10)
            return

        emoji = catmgr.get_category_emoji(slug)
        schedule = {"type": frequency, "time": time, "day": day, "date": date}
        await message.reply(
            f"✅ Added: {emoji} **{slug}**\n"
            f"📅 {catmgr.format_schedule(schedule)}\n📍 {channel.mention}",
            delete_after=30,
        )
        await safe_delete(message)

    async def _text_remove(self, message: discord.Message, slug: str) -> None:
        slug = slug.strip().lower()
        if slug == "bilety-lotnicze":
            await message.reply(
                "🔒 Cannot remove protected category.", delete_after=10,
            )
            return
        removed = await self.bot.db.remove_category_config(
            message.guild.id, slug,
        )
        msg = (
            f"🗑️ Removed: **{slug}**" if removed
            else f"⚠️ **{slug}** not found."
        )
        await message.reply(msg, delete_after=10)
        await safe_delete(message)

    async def _text_status(
        self, message: discord.Message, slug: str, new_status: str,
    ) -> None:
        slug = slug.strip().lower()
        updated = await self.bot.db.update_category_status(
            message.guild.id, slug, new_status,
        )
        if updated:
            icon = "⏸️" if new_status == "paused" else "▶️"
            label = "Paused" if new_status == "paused" else "Resumed"
            await message.reply(f"{icon} {label}: **{slug}**", delete_after=10)
        else:
            await message.reply(f"⚠️ **{slug}** not found.", delete_after=10)
        await safe_delete(message)

    async def _text_trigger(self, message: discord.Message, slug: str) -> None:
        slug = slug.strip().lower()
        cat = await self.bot.db.get_category_by_slug(message.guild.id, slug)
        if not cat:
            await message.reply(f"⚠️ **{slug}** not found.", delete_after=10)
            return
        await message.reply(f"⚡ Triggering: **{slug}**...", delete_after=5)
        await self._process_notification(cat, manual=True)
        await safe_delete(message)

    async def handle_preview(self, message: discord.Message, slug: str) -> None:
        """Text command handler for 'p preview:slug'."""
        result = await self.bot.scraper.get_group_deals(slug, limit=3)
        if not result["success"]:
            await message.reply(
                f"❌ Category **{slug}** not found.", delete_after=10,
            )
            return

        deals = result["deals"]
        if not deals:
            await message.reply(
                f"✅ Found: **{slug}**\n📭 No deals available.",
                delete_after=15,
            )
            return

        embed = discord.Embed(
            title=f"✅ Preview: {slug}",
            description=f"Latest {len(deals)} deals:",
            color=Config.COLOR_SUCCESS,
        )
        for i, deal in enumerate(deals, 1):
            temp = deal.get("temperature", 0)
            icon = temperature_icon(temp)
            embed.add_field(
                name=f"{i}. {deal['title'][:60]}...",
                value=(
                    f"💰 {deal.get('price', '???')} | {icon} {temp}° "
                    f"| 🪐 {deal.get('merchant', 'Unknown')}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Add with: p cat add:{slug} ...")
        await message.reply(embed=embed, delete_after=30)
        await safe_delete(message)

    # --- Slash commands ---

    @category_group.command(
        name="add", description="Add automated category notifications",
    )
    @app_commands.describe(
        slug="Category slug (e.g., podzespoly-komputerowe)",
        frequency="Schedule: daily, weekly, biweekly, monthly",
        time="Time in HH:MM format (24-hour)",
        channel="Target channel for notifications",
        day="Day of week (for weekly/biweekly)",
        date="Day of month 1-31 (for monthly)",
        min_temp="Minimum temperature filter",
        max_price="Maximum price filter in PLN",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def category_add(
        self,
        interaction: discord.Interaction,
        slug: str,
        frequency: str,
        time: str,
        channel: discord.TextChannel,
        day: str | None = None,
        date: int | None = None,
        min_temp: int | None = 0,
        max_price: float | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        ok, err, _ = await self._validate_and_create(
            interaction.guild_id, slug, channel,
            frequency, time, day, date, min_temp or 0, max_price,
        )
        if not ok:
            await interaction.followup.send(err, ephemeral=True)
            return

        emoji = catmgr.get_category_emoji(slug)
        schedule = {"type": frequency, "time": time, "day": day, "date": date}
        embed = discord.Embed(
            title="✅ Category Added!", color=Config.COLOR_SUCCESS,
        )
        embed.add_field(
            name="📂 Category", value=f"{emoji} **{slug}**", inline=False,
        )
        embed.add_field(
            name="📅 Schedule",
            value=catmgr.format_schedule(schedule),
            inline=False,
        )
        embed.add_field(
            name="📍 Channel", value=channel.mention, inline=False,
        )
        if min_temp:
            embed.add_field(
                name="🌡️ Min Temp", value=f"{min_temp}°", inline=True,
            )
        if max_price:
            embed.add_field(
                name="💰 Max Price", value=f"{max_price} zł", inline=True,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @category_group.command(name="remove", description="Remove a category")
    @app_commands.describe(slug="Category slug to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_remove(
        self, interaction: discord.Interaction, slug: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        slug = slug.lower().strip()
        if slug == "bilety-lotnicze":
            await interaction.followup.send(
                "🔒 Cannot remove protected category.", ephemeral=True,
            )
            return
        removed = await self.bot.db.remove_category_config(
            interaction.guild_id, slug,
        )
        if removed:
            await interaction.followup.send(
                f"🗑️ Removed: **{slug}**", ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⚠️ **{slug}** not found.", ephemeral=True,
            )

    @category_group.command(
        name="list", description="Show all active categories",
    )
    async def category_list(
        self, interaction: discord.Interaction,
    ) -> None:
        cats = await self.bot.db.get_guild_categories(interaction.guild_id)
        if not cats:
            await interaction.response.send_message(
                "📭 No categories. Use `/category add`", ephemeral=True,
            )
            return
        embed = self._category_list_embed(cats)
        for field in embed.fields:
            if "bilety-lotnicze" in field.name.lower():
                field.name += " [PROTECTED]"
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @category_group.command(
        name="trigger", description="Manually trigger category notification",
    )
    @app_commands.describe(slug="Category to trigger")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_trigger(
        self, interaction: discord.Interaction, slug: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        slug = slug.lower().strip()
        cat = await self.bot.db.get_category_by_slug(
            interaction.guild_id, slug,
        )
        if not cat:
            await interaction.followup.send(
                f"⚠️ **{slug}** not found.", ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"⚡ Triggering: **{slug}**...", ephemeral=True,
        )
        await self._process_notification(
            cat, manual=True, interaction=interaction,
        )

    @category_group.command(name="pause", description="Pause a category")
    @app_commands.describe(slug="Category to pause")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_pause(
        self, interaction: discord.Interaction, slug: str,
    ) -> None:
        slug = slug.lower().strip()
        if slug == "bilety-lotnicze":
            await interaction.response.send_message(
                "🔒 Cannot pause protected category.", ephemeral=True,
            )
            return
        updated = await self.bot.db.update_category_status(
            interaction.guild_id, slug, "paused",
        )
        if updated:
            await interaction.response.send_message(
                f"⏸️ Paused: **{slug}**", ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ **{slug}** not found.", ephemeral=True,
            )

    @category_group.command(
        name="resume", description="Resume a paused category",
    )
    @app_commands.describe(slug="Category to resume")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_resume(
        self, interaction: discord.Interaction, slug: str,
    ) -> None:
        slug = slug.lower().strip()
        updated = await self.bot.db.update_category_status(
            interaction.guild_id, slug, "active",
        )
        if updated:
            cat = await self.bot.db.get_category_by_slug(
                interaction.guild_id, slug,
            )
            await interaction.response.send_message(
                f"▶️ Resumed: **{slug}**\nNext: {catmgr.format_schedule(cat)}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⚠️ **{slug}** not found.", ephemeral=True,
            )

    @category_group.command(
        name="preview", description="Preview deals before adding category",
    )
    @app_commands.describe(slug="Category slug to preview")
    async def category_preview(
        self, interaction: discord.Interaction, slug: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        slug = slug.lower().strip()
        result = await self.bot.scraper.get_group_deals(slug, limit=3)

        if not result["success"]:
            await interaction.followup.send(
                f"❌ **{slug}** not found on Pepper.pl", ephemeral=True,
            )
            return

        deals = result["deals"]
        if not deals:
            await interaction.followup.send(
                f"✅ Found: **{slug}**\n📭 No deals currently.", ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"✅ Preview: {slug}",
            description=f"Latest {len(deals)} deals:",
            color=Config.COLOR_SUCCESS,
        )
        for i, deal in enumerate(deals, 1):
            temp = deal.get("temperature", 0)
            icon = temperature_icon(temp)
            embed.add_field(
                name=f"{i}. {deal['title'][:60]}...",
                value=(
                    f"💰 {deal.get('price', '???')} | {icon} {temp}° "
                    f"| 🪐 {deal.get('merchant', 'Unknown')}"
                ),
                inline=False,
            )
        embed.set_footer(text=f"Add with: /category add {slug} ...")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @category_add.error
    async def category_add_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ Admin permission required.", ephemeral=True,
            )
        else:
            logger.error("Category add error: %s", error, exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ Unexpected error.", ephemeral=True,
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CategoriesCog(bot))
