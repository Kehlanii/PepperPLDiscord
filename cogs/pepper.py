import asyncio
import datetime
import logging
from collections import defaultdict
from functools import wraps
from typing import Any, Callable, Dict, Optional, List

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.alerts import AlertsManager
from utils.category_manager import CategoryManager
from utils.config import Config
from utils.scraper import PepperScraper
from utils.views import DealPaginator

logger = logging.getLogger("PepperBot.Cogs")

CATEGORY_STAGGER_DELAY = 2
MAX_DEALS_PER_NOTIFICATION = 10
MAX_CATEGORIES_PER_GUILD = 20
CLEANUP_INTERVAL_HOURS = 24
CLEANUP_DAYS_OLD = 30


def text_command_error_handler(func):
    @wraps(func)
    async def wrapper(self, message: discord.Message, *args, **kwargs):
        try:
            await func(self, message, *args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}", exc_info=True)
            try:
                await message.reply(f"⚠️ Error: {e}", delete_after=10)
            except:
                pass
    return wrapper


class PepperCommands(commands.Cog):
    pepperwatch_group = app_commands.Group(
        name="pepperwatch", description="Zarządzaj powiadomieniami o okazjach"
    )
    
    category_group = app_commands.Group(
        name="category",
        description="Manage automated category notifications"
    )

    def __init__(self, bot):
        self.bot = bot
        self.alerts_manager = AlertsManager(self.bot.db)
        self.category_manager = CategoryManager(self.bot.db)

        self.flight_deals_task.start()
        self.alerts_task.start()
        self.category_notification_task.start()
        self.cleanup_task.start()

    def cog_unload(self):
        self.flight_deals_task.cancel()
        self.alerts_task.cancel()
        self.category_notification_task.cancel()
        self.cleanup_task.cancel()

    @tasks.loop(time=datetime.time(hour=Config.FLIGHT_SCHEDULE_HOUR, minute=0))
    async def flight_deals_task(self):
        await self.process_flight_deals(manual_trigger=False)

    @flight_deals_task.before_loop
    async def before_flight_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=Config.WATCH_INTERVAL_MINUTES)
    async def alerts_task(self):
        await self.process_alerts()

    @alerts_task.before_loop
    async def before_alerts_task(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def category_notification_task(self):
        try:
            categories = await self.bot.db.get_active_categories_for_schedule()
            
            if not categories:
                return
            
            logger.info(f"Checking {len(categories)} active categories for scheduled runs")
            
            to_process = [cat for cat in categories if self.category_manager.should_run_now(cat)]
            
            if not to_process:
                return
            
            logger.info(f"Processing {len(to_process)} categories")
            
            for i, category in enumerate(to_process):
                try:
                    if i > 0:
                        await asyncio.sleep(CATEGORY_STAGGER_DELAY)
                    
                    await self.process_category_notification(category)
                    
                except Exception as e:
                    logger.error(f"Error processing category {category['slug']}: {e}", exc_info=True)
                    await self.bot.db.update_category_stats(category['id'], 0, 0, errors=1)
        
        except Exception as e:
            logger.error(f"Error in category notification task: {e}", exc_info=True)
    
    @category_notification_task.before_loop
    async def before_category_task(self):
        await self.bot.wait_until_ready()
    
    @tasks.loop(hours=CLEANUP_INTERVAL_HOURS)
    async def cleanup_task(self):
        try:
            logger.info("Running scheduled cleanup task...")
            
            deleted_deals = await self.bot.db.cleanup_old_deals(days=CLEANUP_DAYS_OLD)
            deleted_category_deals = await self.bot.db.cleanup_category_deals(days=CLEANUP_DAYS_OLD)
            
            logger.info(
                f"Cleanup complete: {deleted_deals} flight deals, "
                f"{deleted_category_deals} category deals removed"
            )
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}", exc_info=True)
    
    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()
    
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.content.startswith('p '):
            return
        
        content = message.content[2:].strip()
        if not content:
            return
        
        handlers = {
            'watch:': lambda: self._handle_watch_command(message, content),
            'unwatch:': lambda: self._handle_unwatch_command(message, content),
            'group:': lambda: self._handle_group_command(message, content),
            'preview:': lambda: self._handle_preview_command(message, content),
            'cat ': lambda: self._handle_category_command(message, content),
        }
        
        exact_handlers = {
            'alerts': lambda: self._handle_list_command(message),
            'list': lambda: self._handle_list_command(message),
            'hot': lambda: self._handle_hot_command(message),
            'fly': lambda: self._handle_fly_command(message),
        }
        
        try:
            if content.startswith('clean'):
                await self._handle_clean_command(message, content)
                return
            
            for prefix, handler in handlers.items():
                if content.startswith(prefix):
                    await handler()
                    return
            
            if content in exact_handlers:
                await exact_handlers[content]()
                return
            
            await self._handle_search_command(message, content)
            
        except Exception as e:
            logger.error(f"Error in text command handler: {e}", exc_info=True)
            try:
                await message.reply(f"⚠️ Error processing command: {e}", delete_after=10)
            except:
                pass

    def parse_price_from_text(self, text: str) -> tuple[str, Optional[float], Optional[str]]:
        for op, op_type in [('<', 'max'), ('>', 'min')]:
            if op in text:
                parts = text.split(op, 1)
                query = parts[0].strip()
                try:
                    return query, float(parts[1].strip()), op_type
                except (ValueError, IndexError):
                    return query, None, None
        return text.strip(), None, None

    async def safe_delete_message(self, message: discord.Message):
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        except Exception as e:
            logger.debug(f"Could not delete message: {e}")

    def get_temperature_icon(self, temp: int) -> str:
        return '🌋' if temp > 500 else ('🔥' if temp > 300 else '❄️')

    async def _add_alert_shared(self, user_id: int, query: str, max_price: Optional[float]) -> tuple[bool, str]:
        current = await self.alerts_manager.get_alerts(user_id)
        if len(current) >= 10:
            return False, "❌ Max 10 alerts. Remove some first."
        
        added = await self.alerts_manager.add_alert(user_id, query, max_price)
        
        if not added:
            return False, "⚠️ Error adding alert."
        
        msg = f"✅ Watching: **{query}**"
        if max_price:
            msg += f" (< {max_price} zł)"
        msg += "\n🔔 Checking every 15 minutes"
        return True, msg

    async def _remove_alert_shared(self, user_id: int, query: str) -> tuple[bool, str]:
        removed = await self.alerts_manager.remove_alert(user_id, query)
        
        if removed:
            return True, f"🗑️ Stopped watching: **{query}**"
        else:
            return False, f"⚠️ Alert **{query}** not found.\nUse `p alerts` to see your list."

    def _build_alerts_embed(self, alerts: List[Dict]) -> discord.Embed:
        embed = discord.Embed(
            title="🔔 Your Alerts",
            description="Watching these queries:",
            color=Config.COLOR_PRIMARY,
        )
        
        for i, a in enumerate(alerts, 1):
            price_info = f"**< {a['max_price']} zł**" if a["max_price"] else "Any price"
            embed.add_field(name=f"{i}. {a['query']}", value=f"💰 {price_info}", inline=False)
        
        embed.set_footer(text="Use p unwatch:query to remove")
        return embed

    def _build_category_list_embed(self, categories: List[Dict]) -> discord.Embed:
        embed = discord.Embed(
            title="📋 Active Categories",
            description=f"Managing {len(categories)} automated notifications",
            color=Config.COLOR_PRIMARY
        )
        
        for i, cat in enumerate(categories, 1):
            emoji = self.category_manager.get_category_emoji(cat['slug'])
            
            filters = []
            if cat.get('min_temperature', 0) > 0:
                filters.append(f"🌡️ Min: {cat['min_temperature']}°")
            if cat.get('max_price'):
                filters.append(f"💰 Max: {cat['max_price']} zł")
            
            filter_str = " | ".join(filters) if filters else "No filters"
            schedule_str = self.category_manager.format_schedule(cat)
            status_emoji = "✅" if cat['status'] == 'active' else "⏸️"
            
            value = f"{status_emoji} {schedule_str}\n📍 <#{cat['channel_id']}>\n{filter_str}"
            name = f"{i}. {emoji} {cat['slug']}"
            
            embed.add_field(name=name, value=value, inline=False)
        
        return embed

    async def _validate_and_create_category(
        self,
        guild_id: int,
        slug: str,
        channel: discord.TextChannel,
        frequency: str,
        time: str,
        day: Optional[str],
        date: Optional[int],
        min_temp: int,
        max_price: Optional[float]
    ) -> tuple[bool, Optional[str], Optional[int]]:
        slug = slug.lower().strip()
        
        existing = await self.bot.db.get_category_by_slug(guild_id, slug)
        if existing:
            return False, f"⚠️ Category **{slug}** already exists.", None
        
        guild_categories = await self.bot.db.get_guild_categories(guild_id)
        if len(guild_categories) >= MAX_CATEGORIES_PER_GUILD:
            return False, f"❌ Maximum {MAX_CATEGORIES_PER_GUILD} categories per server.", None
        
        valid, error = await self.category_manager.validate_slug(self.scraper, slug)
        if not valid:
            return False, error, None
        
        valid, error = await self.category_manager.validate_channel_permissions(self.bot, channel)
        if not valid:
            return False, error, None
        
        valid, schedule, error = await self.category_manager.parse_schedule(frequency, time, day, date)
        if not valid:
            return False, error, None
        
        category_id = await self.bot.db.add_category_config(
            guild_id=guild_id,
            slug=slug,
            channel_id=channel.id,
            schedule_type=schedule['type'],
            schedule_time=schedule['time'],
            schedule_day=schedule['day'],
            schedule_date=schedule['date'],
            min_temperature=min_temp,
            max_price=max_price
        )
        
        if not category_id:
            return False, "❌ Database error.", None
        
        return True, None, category_id

    @text_command_error_handler
    async def _handle_watch_command(self, message: discord.Message, content: str):
        args = content[6:].strip()
        query, price, price_type = self.parse_price_from_text(args)
        
        if not query:
            await message.reply("❌ Usage: `p watch:query` or `p watch:query < price`", delete_after=10)
            return
        
        max_price = price if price_type == 'max' else None
        success, msg = await self._add_alert_shared(message.author.id, query, max_price)
        await message.reply(msg, delete_after=15)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_unwatch_command(self, message: discord.Message, content: str):
        query = content[8:].strip()
        
        if not query:
            await message.reply("❌ Usage: `p unwatch:query`", delete_after=10)
            return
        
        success, msg = await self._remove_alert_shared(message.author.id, query)
        await message.reply(msg, delete_after=10)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_list_command(self, message: discord.Message):
        alerts = await self.alerts_manager.get_alerts(message.author.id)
        
        if not alerts:
            await message.reply("🔭 No active alerts.", delete_after=10)
            await self.safe_delete_message(message)
            return
        
        embed = self._build_alerts_embed(alerts)
        await message.reply(embed=embed)
        await self.safe_delete_message(message)

    async def _handle_search_generic(
        self,
        message: discord.Message,
        scraper_method: Callable,
        method_args: tuple,
        title_template: str,
        error_msg: str
    ):
        try:
            from utils.deal_filter import DealFilter
            
            result = await scraper_method(*method_args)
            
            if not result["success"]:
                await message.reply(f"❌ Error: {result.get('error', 'Unknown')}", delete_after=10)
                return
            
            all_deals = result["deals"]
            deals = DealFilter.filter_deals(
                all_deals,
                check_freshness=True,
                check_temperature=True,
                check_price=True
            )
            
            if not deals:
                if all_deals:
                    await message.reply(
                        f"🔍 Found {len(all_deals)} deals, but none met quality standards.\n"
                        f"Filters: Recent (<24h), Hot (≥50°), Valid price",
                        delete_after=20
                    )
                else:
                    await message.reply(error_msg, delete_after=10)
                return
            
            view = DealPaginator(deals, message.author)
            embed = view.get_initial_embed()
            
            await message.reply(content=title_template.format(count=len(deals)), embed=embed, view=view)
            await self.safe_delete_message(message)
        
        except Exception as e:
            logger.error(f"Error in search command: {e}", exc_info=True)
            await message.reply(f"⚠️ Error: {e}", delete_after=10)

    async def _handle_search_command(self, message: discord.Message, query: str):
        await self._handle_search_generic(
            message,
            self.scraper.search_deals,
            (query, Config.DEFAULT_SEARCH_LIMIT),
            f"**🌶️ Found {{count}} deals for: {query}**",
            f"🤷 No deals found for: **{query}**"
        )

    async def _handle_hot_command(self, message: discord.Message):
        await self._handle_search_generic(
            message,
            self.scraper.get_hot_deals,
            (Config.DEFAULT_SEARCH_LIMIT,),
            "**🔥 Top {count} hot deals!**",
            "🤷 No hot deals found."
        )

    async def _handle_group_command(self, message: discord.Message, content: str):
        slug = content[6:].strip().lower().replace(' ', '-')
        
        if not slug:
            await message.reply("❌ Usage: `p group:slug`", delete_after=10)
            return
        
        await self._handle_search_generic(
            message,
            self.scraper.get_group_deals,
            (slug, Config.DEFAULT_SEARCH_LIMIT),
            f"**📂 Top {{count}} deals from: {slug}**",
            f"🤷 No deals in category: **{slug}**"
        )

    @text_command_error_handler
    async def _handle_preview_command(self, message: discord.Message, content: str):
        slug = content[8:].strip().lower()
        
        if not slug:
            await message.reply("❌ Usage: `p preview:slug`", delete_after=10)
            return
        
        result = await self.scraper.get_group_deals(slug, limit=3)
        
        if not result['success']:
            await message.reply(f"❌ Category **{slug}** not found.", delete_after=10)
            return
        
        deals = result['deals']
        if not deals:
            await message.reply(f"✅ Category found: **{slug}**\n📭 No deals currently available.", delete_after=15)
            return
        
        embed = discord.Embed(
            title=f"✅ Preview: {slug}",
            description=f"Latest {len(deals)} deals:",
            color=Config.COLOR_SUCCESS
        )
        
        for i, deal in enumerate(deals, 1):
            temp = deal.get('temperature', 0)
            icon = self.get_temperature_icon(temp)
            
            value = f"💰 {deal.get('price', '???')} | {icon} {temp}° | 🪐 {deal.get('merchant', 'Unknown')}"
            embed.add_field(
                name=f"{i}. {deal['title'][:60]}...",
                value=value,
                inline=False
            )
        
        embed.set_footer(text=f"Add with: p cat add:{slug} ...")
        await message.reply(embed=embed, delete_after=30)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_fly_command(self, message: discord.Message):
        if not message.author.guild_permissions.administrator:
            await message.reply("❌ Admin only command.", delete_after=10)
            return
        
        await message.reply("⚡ Triggering flight deals...", delete_after=5)
        await self.process_flight_deals(manual_trigger=True)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_clean_command(self, message: discord.Message, content: str):
        if not message.channel.permissions_for(message.guild.me).manage_messages:
            await message.reply("❌ Missing 'Manage Messages' permission.", delete_after=10)
            return
        
        parts = content.split()
        limit = int(parts[1]) if len(parts) > 1 else 20
        
        deleted = await message.channel.purge(limit=limit, check=lambda m: m.author == self.bot.user)
        await message.reply(f"🗑️ Deleted {len(deleted)} messages", delete_after=5)
        await self.safe_delete_message(message)

    async def _handle_category_command(self, message: discord.Message, content: str):
        if not message.author.guild_permissions.administrator:
            await message.reply("❌ Admin only command.", delete_after=10)
            return
        
        cat_content = content[4:].strip()
        
        cat_handlers = {
            'list': lambda: self._handle_cat_list(message),
            'add:': lambda: self._handle_cat_add(message, cat_content[4:]),
            'rm:': lambda: self._handle_cat_remove(message, cat_content[3:]),
            'pause:': lambda: self._handle_cat_status_change(message, cat_content[6:], 'paused'),
            'resume:': lambda: self._handle_cat_status_change(message, cat_content[7:], 'active'),
            'run:': lambda: self._handle_cat_trigger(message, cat_content[4:]),
        }
        
        try:
            if cat_content == 'list':
                await cat_handlers['list']()
            else:
                for prefix, handler in cat_handlers.items():
                    if cat_content.startswith(prefix) and prefix != 'list':
                        await handler()
                        return
                
                await message.reply("❌ Usage: `p cat [list|add:slug|rm:slug|pause:slug|resume:slug|run:slug]`", delete_after=10)
        
        except Exception as e:
            logger.error(f"Error in category command: {e}", exc_info=True)
            await message.reply(f"⚠️ Error: {e}", delete_after=10)

    @text_command_error_handler
    async def _handle_cat_list(self, message: discord.Message):
        categories = await self.bot.db.get_guild_categories(message.guild.id)
        
        if not categories:
            await message.reply("📭 No categories configured.\nUse `p cat add:slug ...` to create one!", delete_after=10)
            await self.safe_delete_message(message)
            return
        
        embed = self._build_category_list_embed(categories)
        await message.reply(embed=embed)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_cat_add(self, message: discord.Message, args: str):
        parts = args.split()
        if len(parts) < 4:
            await message.reply("❌ Usage: `p cat add:slug frequency time #channel [day] [min:temp] [max:price]`", delete_after=15)
            return
        
        slug = parts[0].lower().strip()
        frequency = parts[1].lower()
        time = parts[2]
        
        channel = None
        for word in parts:
            if word.startswith('<#') and word.endswith('>'):
                channel_id = int(word[2:-1])
                channel = self.bot.get_channel(channel_id)
                break
        
        if not channel:
            await message.reply("❌ Channel not found. Use #channel mention.", delete_after=10)
            return
        
        day = None
        date = None
        min_temp = 0
        max_price = None
        
        for part in parts:
            if part.startswith('min:'):
                min_temp = int(part[4:])
            elif part.startswith('max:'):
                max_price = float(part[4:])
            elif part.lower() in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                day = part.lower()
            elif part.isdigit() and 1 <= int(part) <= 31 and frequency == 'monthly':
                date = int(part)
        
        success, error, category_id = await self._validate_and_create_category(
            message.guild.id, slug, channel, frequency, time, day, date, min_temp, max_price
        )
        
        if not success:
            await message.reply(error, delete_after=10)
            return
        
        emoji = self.category_manager.get_category_emoji(slug)
        schedule = {'type': frequency, 'time': time, 'day': day, 'date': date}
        
        msg = f"✅ Category added: {emoji} **{slug}**\n"
        msg += f"📅 {self.category_manager.format_schedule(schedule)}\n"
        msg += f"📍 {channel.mention}"
        
        await message.reply(msg, delete_after=30)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_cat_remove(self, message: discord.Message, slug: str):
        slug = slug.strip().lower()
        
        if slug == 'bilety-lotnicze':
            await message.reply("🔒 Cannot remove protected category.", delete_after=10)
            return
        
        removed = await self.bot.db.remove_category_config(message.guild.id, slug)
        
        msg = f"🗑️ Removed category: **{slug}**" if removed else f"⚠️ Category **{slug}** not found."
        await message.reply(msg, delete_after=10)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_cat_status_change(self, message: discord.Message, slug: str, new_status: str):
        slug = slug.strip().lower()
        
        updated = await self.bot.db.update_category_status(message.guild.id, slug, new_status)
        
        status_emoji = "⏸️" if new_status == 'paused' else "▶️"
        status_text = "Paused" if new_status == 'paused' else "Resumed"
        
        msg = f"{status_emoji} {status_text}: **{slug}**" if updated else f"⚠️ Category **{slug}** not found."
        await message.reply(msg, delete_after=10)
        await self.safe_delete_message(message)

    @text_command_error_handler
    async def _handle_cat_trigger(self, message: discord.Message, slug: str):
        slug = slug.strip().lower()
        
        category = await self.bot.db.get_category_by_slug(message.guild.id, slug)
        if not category:
            await message.reply(f"⚠️ Category **{slug}** not found.", delete_after=10)
            return
        
        await message.reply(f"⚡ Triggering: **{slug}**...", delete_after=5)
        await self.process_category_notification(category, manual_trigger=True)
        await self.safe_delete_message(message)
    
    async def process_category_notification(
        self,
        category: Dict[str, Any],
        manual_trigger: bool = False,
        interaction: discord.Interaction = None
    ):
        try:
            channel = self.bot.get_channel(category['channel_id'])
            if not channel:
                logger.warning(f"Channel {category['channel_id']} not found for category {category['slug']}")
                if not manual_trigger:
                    await self.bot.db.update_category_status(
                        category['guild_id'], category['slug'], 'disabled'
                    )
                return
            
            result = await self.scraper.get_group_deals(category['slug'], limit=20)
            
            if not result['success']:
                error_detail = result.get('error', 'Unknown error')
                logger.error(f"Failed to scrape {category['slug']}: {error_detail}")
                if interaction:
                    await interaction.followup.send(
                        "❌ Failed to fetch deals. Please try again later.", ephemeral=True
                    )
                await self.bot.db.update_category_stats(category['id'], 0, 0, errors=1)
                return
            
            deals = result['deals']
            if not deals:
                logger.info(f"No deals found for {category['slug']}")
                if interaction:
                    await interaction.followup.send(
                        f"🤷 No deals found for **{category['slug']}**", ephemeral=True
                    )
                await self.bot.db.update_category_stats(category['id'], 0, 0)
                return
            
            new_deals = []
            batch_to_mark = []
            
            for deal in deals:
                deal_id = deal['link']
                
                if category.get('min_temperature', 0) > 0:
                    if deal.get('temperature', 0) < category['min_temperature']:
                        continue
                
                if category.get('max_price'):
                    deal_price = self._parse_price(deal.get('price'))
                    if deal_price > 0 and deal_price > category['max_price']:
                        continue
                
                is_sent = await self.bot.db.is_category_deal_sent(category['id'], deal_id)
                
                if manual_trigger or not is_sent:
                    new_deals.append(deal)
                    if not manual_trigger:
                        batch_to_mark.append((category['id'], deal_id))
            
            if batch_to_mark:
                await self.bot.db.mark_category_deals_sent_batch(batch_to_mark)
            
            if not new_deals:
                logger.info(f"No new deals for {category['slug']}")
                if interaction:
                    await interaction.followup.send(
                        f"No new deals since last check for **{category['slug']}**", ephemeral=True
                    )
                await self.bot.db.update_category_stats(category['id'], len(deals), 0)
                return
            
            new_deals.sort(key=lambda x: x.get('temperature', 0), reverse=True)
            top_deals = new_deals[:MAX_DEALS_PER_NOTIFICATION]
            
            emoji = self.category_manager.get_category_emoji(category['slug'])
            
            embed = discord.Embed(
                title=f"{emoji} {category.get('name', category['slug'])}",
                description=f"Found **{len(new_deals)}** new deals. Here are the hottest:",
                color=Config.COLOR_PRIMARY
            )
            
            for i, deal in enumerate(top_deals, 1):
                price = deal.get('price') or '???'
                temp = deal.get('temperature', 0)
                merchant = deal.get('merchant', 'Unknown')
                
                icon = self.get_temperature_icon(temp)
                
                value_str = f"💰 **{price}** | {icon} {temp}° | 🪐 {merchant}\n[🔗 View deal]({deal['link']})"
                
                embed.add_field(
                    name=f"{i}. {deal['title'][:80]}...",
                    value=value_str,
                    inline=False
                )
            
            schedule_str = self.category_manager.format_schedule(category)
            embed.set_footer(text=f"Pepper.pl • {schedule_str}")
            
            if top_deals and top_deals[0].get('image_url'):
                embed.set_thumbnail(url=top_deals[0]['image_url'])
            
            await channel.send(embed=embed)
            
            await self.bot.db.update_category_last_run(category['id'])
            await self.bot.db.update_category_stats(category['id'], len(deals), len(new_deals))
            
            if not manual_trigger:
                logger.info(f"Sent {len(top_deals)} deals for category {category['slug']}")
            elif interaction:
                await interaction.followup.send(
                    f"✅ Sent {len(top_deals)} deals to {channel.mention}", ephemeral=True
                )
        
        except Exception as e:
            logger.error(f"Error in category notification: {e}", exc_info=True)
            if interaction:
                await interaction.followup.send(
                    "⚠️ An unexpected error occurred. Please try again later.", ephemeral=True
                )
    
    def _parse_price(self, price_str: Optional[str]) -> float:
        if not price_str:
            return 0.0
        try:
            clean = price_str.lower().replace('zł', '').replace(' ', '').replace(',', '.')
            if 'darm' in clean or 'free' in clean or 'bezpłatn' in clean:
                return 0.0
            return float(clean)
        except ValueError:
            return 0.0

    async def process_alerts(self):
        try:
            notifications = await self.alerts_manager.check_alerts(self.scraper)
            
            grouped = defaultdict(lambda: defaultdict(list))
            for notif in notifications:
                grouped[notif["user_id"]][notif["query"]].append(notif["deal"])
            
            for user_id, queries_dict in grouped.items():
                user = self.bot.get_user(user_id)
                if not user:
                    try:
                        user = await self.bot.fetch_user(user_id)
                    except (discord.NotFound, Exception) as e:
                        logger.warning(f"Could not fetch user {user_id}: {e}")
                        continue
                
                for query, deals in queries_dict.items():
                    try:
                        deals_sorted = sorted(deals, key=lambda d: d.get('temperature', 0), reverse=True)
                        top_deals = deals_sorted[:5]
                        
                        embed = discord.Embed(
                            title=f"🚨 {len(deals)} {'nowa okazja' if len(deals) == 1 else 'nowych okazji'} dla: {query}",
                            color=Config.COLOR_SUCCESS
                        )
                        
                        for i, deal in enumerate(top_deals, 1):
                            temp = deal.get('temperature', 0)
                            icon = self.get_temperature_icon(temp)
                            
                            value = f"💰 **{deal['price']}** | {icon} {temp}°\n[🔗 Zobacz okazję]({deal['link']})"
                            
                            embed.add_field(
                                name=f"{i}. {deal['title'][:70]}...",
                                value=value,
                                inline=False
                            )
                        
                        if top_deals[0].get('image_url'):
                            embed.set_thumbnail(url=top_deals[0]['image_url'])
                        
                        embed.set_footer(text="PepperWatch • Sprawdzam co 15 minut")
                        
                        await user.send(embed=embed)
                        logger.info(f"Sent {len(top_deals)} deals to {user.name} for query '{query}'")
                        
                        await asyncio.sleep(0.5)
                    
                    except discord.Forbidden:
                        logger.warning(f"Cannot send DM to {user.name} ({user_id})")
                    except Exception as e:
                        logger.error(f"Error sending alert to {user_id}: {e}", exc_info=True)
        
        except Exception as e:
            logger.error(f"Error in alerts task: {e}", exc_info=True)

    async def process_flight_deals(
        self, manual_trigger: bool = False, interaction: discord.Interaction = None
    ):
        channel_id = Config.FLIGHT_CHANNEL_ID

        target_channel = None
        if interaction:
            target_channel = interaction.channel
        else:
            target_channel = self.bot.get_channel(channel_id)

        if not target_channel:
            msg = f"Flight channel {channel_id} not found."
            logger.warning(msg)
            if interaction:
                await interaction.followup.send(f"⚠️ {msg}", ephemeral=True)
            return

        try:
            result = await self.scraper.get_flight_deals(limit=20)

            if not result["success"]:
                if interaction:
                    await interaction.followup.send(
                        f"❌ Błąd pobierania: {result.get('error')}", ephemeral=True
                    )
                return

            deals = result["deals"]
            if not deals:
                if interaction:
                    await interaction.followup.send(
                        "🤷 Nie znaleziono żadnych okazji lotniczych.", ephemeral=True
                    )
                return

            new_deals = []
            for deal in deals:
                deal_id = deal["link"]
                is_sent = await self.bot.db.is_deal_sent(deal_id)

                if manual_trigger or not is_sent:
                    new_deals.append(deal)
                    if not manual_trigger:
                        await self.bot.db.add_sent_deal(deal_id)

            if not new_deals:
                logger.info("No new flight deals found.")
                if interaction:
                    await interaction.followup.send(
                        "Brak nowych okazji od ostatniego sprawdzenia.", ephemeral=True
                    )
                return

            new_deals.sort(key=lambda x: x.get("temperature", 0), reverse=True)
            top_deals = new_deals[:MAX_DEALS_PER_NOTIFICATION]

            embed = discord.Embed(
                title=f"✈️ Dzienny Raport Lotniczy - {datetime.date.today()}",
                description=f"Znaleziono **{len(new_deals)}** okazji. Oto najlepsze z nich:",
                color=Config.COLOR_PRIMARY,
            )

            for i, deal in enumerate(top_deals, 1):
                price = deal.get("price") or "???"
                temp = deal.get("temperature", 0)
                merchant = deal.get("merchant", "Unknown")

                icon = self.get_temperature_icon(temp)

                value_str = f"💰 **{price}** | {icon} {temp}° | 🏪 {merchant}\n[🔗 Zobacz okazję]({deal['link']})"

                embed.add_field(name=f"{i}. {deal['title'][:80]}...", value=value_str, inline=False)

            embed.set_footer(text="Pepper.pl Bot • Aktualizacja codzienna o 08:00")

            if top_deals and top_deals[0].get("image_url"):
                embed.set_thumbnail(url=top_deals[0]["image_url"])

            await target_channel.send(embed=embed)

            if not manual_trigger:
                logger.info(f"Sent flight digest with {len(top_deals)} deals.")
            elif interaction:
                await interaction.followup.send("✅ Wysłano raport lotniczy.", ephemeral=True)

        except Exception as e:
            logger.error(f"Error in flight task: {e}", exc_info=True)
            if interaction:
                await interaction.followup.send(f"⚠️ Wystąpił błąd: {e}", ephemeral=True)

    @property
    def scraper(self) -> PepperScraper:
        return PepperScraper(self.bot.session)

    async def _send_deals(
        self,
        interaction: discord.Interaction,
        result: Dict[str, Any],
        title_success: str,
        title_empty: str,
    ):
        if not result["success"]:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="⚠️ Błąd",
                    description=f"Wystąpił błąd podczas pobierania danych: {result.get('error', 'Nieznany błąd')}",
                    color=Config.COLOR_WARNING,
                )
            )
            return

        deals = result["deals"]
        if not deals:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🤷 Brak wyników", description=title_empty, color=Config.COLOR_NEUTRAL
                )
            )
            return

        view = DealPaginator(deals, interaction.user)
        embed = view.get_initial_embed()

        await interaction.followup.send(
            content=f"**{title_success.format(count=len(deals))}**", embed=embed, view=view
        )

    @app_commands.command(name="pepper", description="Szukaj okazji na Pepper.pl")
    @app_commands.describe(query="Czego szukasz? (np. lego, rtx 4070)")
    async def search_pepper(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        result = await self.scraper.search_deals(query, limit=Config.DEFAULT_SEARCH_LIMIT)

        await self._send_deals(
            interaction,
            result,
            title_success=f"🌶️ Znaleziono {{count}} okazji dla: {query}",
            title_empty=f"Nie znaleziono okazji dla: **{query}**",
        )

    @app_commands.command(name="pepperhot", description="Najgorętsze okazje ze strony głównej")
    async def hot_pepper(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await self.scraper.get_hot_deals(limit=Config.DEFAULT_SEARCH_LIMIT)

        await self._send_deals(
            interaction,
            result,
            title_success="🔥 Top {count} najgorętszych okazji!",
            title_empty="Brak gorących okazji na stronie głównej.",
        )

    @app_commands.command(
        name="pepper_group", description="Pobierz okazje z konkretnej grupy/kategorii"
    )
    @app_commands.describe(group="Slug grupy (np. elektronika, gry, dom-i-ogrod)")
    async def group_pepper(self, interaction: discord.Interaction, group: str):
        await interaction.response.defer()
        group = group.lower().strip().replace(" ", "-")
        result = await self.scraper.get_group_deals(group, limit=Config.DEFAULT_SEARCH_LIMIT)

        await self._send_deals(
            interaction,
            result,
            title_success=f"📂 Top {{count}} okazji z grupy: {group}",
            title_empty=f"Brak okazji w grupie: **{group}**. Sprawdź czy nazwa jest poprawna.",
        )

    @app_commands.command(name="flynow", description="[Admin] Ręczne wywołanie raportu lotniczego")
    async def fly_now(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await self.process_flight_deals(manual_trigger=True, interaction=interaction)

    @app_commands.command(name="pepperclean", description="Usuwa ostatnie wiadomości bota")
    @app_commands.describe(limit="Ile wiadomości sprawdzić? (domyślnie 20)")
    async def clean_pepper(self, interaction: discord.Interaction, limit: int = 20):
        await interaction.response.defer(ephemeral=True)

        try:
            deleted = await interaction.channel.purge(limit=limit, check=lambda m: m.author == self.bot.user)
            await interaction.followup.send(
                f"🗑️ Usunięto {len(deleted)} moich wiadomości (sprawdzono {limit}).", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Błąd: Nie mam uprawnień 'Manage Messages'.", ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"⚠️ Wystąpił błąd: {e}", ephemeral=True)

    @pepperwatch_group.command(
        name="add", description="Dodaj powiadomienie (np. 'rtx 4070' < 3000)"
    )
    @app_commands.describe(query="Fraza do wyszukania", max_price="Maksymalna cena (opcjonalnie)")
    async def pw_add(
        self, interaction: discord.Interaction, query: str, max_price: Optional[float] = None
    ):
        await interaction.response.defer(ephemeral=True)
        success, msg = await self._add_alert_shared(interaction.user.id, query, max_price)
        await interaction.followup.send(msg, ephemeral=True)

    @pepperwatch_group.command(name="list", description="Pokaż moje aktywne powiadomienia")
    async def pw_list(self, interaction: discord.Interaction):
        alerts = await self.alerts_manager.get_alerts(interaction.user.id)
        if not alerts:
            await interaction.response.send_message(
                "🔭 Nie masz żadnych aktywnych powiadomień.", ephemeral=True
            )
            return

        embed = self._build_alerts_embed(alerts)
        embed.set_footer(text="Użyj /pepperwatch remove [fraza] aby usunąć.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @pepperwatch_group.command(name="remove", description="Usuń powiadomienie")
    @app_commands.describe(query="Fraza do usunięcia (dokładna nazwa z listy)")
    async def pw_remove(self, interaction: discord.Interaction, query: str):
        success, msg = await self._remove_alert_shared(interaction.user.id, query)
        
        if not success:
            msg = msg.replace("p alerts", "/pepperwatch list")
        
        await interaction.response.send_message(msg, ephemeral=True)

    @category_group.command(name="add", description="Add automated category notifications")
    @app_commands.describe(
        slug="Category slug (e.g., podzespoly-komputerowe)",
        frequency="Schedule: daily, weekly, biweekly, monthly",
        time="Time in HH:MM format (24-hour)",
        channel="Target channel for notifications",
        day="Day of week (for weekly/biweekly)",
        date="Day of month 1-31 (for monthly)",
        min_temp="Minimum temperature filter",
        max_price="Maximum price filter in PLN"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def category_add(
        self,
        interaction: discord.Interaction,
        slug: str,
        frequency: str,
        time: str,
        channel: discord.TextChannel,
        day: Optional[str] = None,
        date: Optional[int] = None,
        min_temp: Optional[int] = 0,
        max_price: Optional[float] = None
    ):
        await interaction.response.defer(ephemeral=True)
        
        success, error, category_id = await self._validate_and_create_category(
            interaction.guild_id, slug, channel, frequency, time, day, date, min_temp or 0, max_price
        )
        
        if not success:
            await interaction.followup.send(error, ephemeral=True)
            return
        
        emoji = self.category_manager.get_category_emoji(slug)
        schedule = {'type': frequency, 'time': time, 'day': day, 'date': date}
        
        embed = discord.Embed(
            title="✅ Category Added Successfully!",
            color=Config.COLOR_SUCCESS
        )
        
        embed.add_field(name="📂 Category", value=f"{emoji} **{slug}**", inline=False)
        embed.add_field(name="📅 Schedule", value=self.category_manager.format_schedule(schedule), inline=False)
        embed.add_field(name="📍 Channel", value=channel.mention, inline=False)
        
        if min_temp:
            embed.add_field(name="🌡️ Min Temperature", value=f"{min_temp}°", inline=True)
        if max_price:
            embed.add_field(name="💰 Max Price", value=f"{max_price} zł", inline=True)
        
        embed.set_footer(text="Use /category list to see all categories")
        
        await interaction.followup.send(embed=embed, ephemeral=True)
    
    @category_group.command(name="remove", description="Remove a category")
    @app_commands.describe(slug="Category slug to remove")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_remove(self, interaction: discord.Interaction, slug: str):
        await interaction.response.defer(ephemeral=True)
        
        slug = slug.lower().strip()
        
        if slug == 'bilety-lotnicze':
            await interaction.followup.send(
                "🔒 Cannot remove protected system category: **bilety-lotnicze**",
                ephemeral=True
            )
            return
        
        removed = await self.bot.db.remove_category_config(interaction.guild_id, slug)
        if removed:
            await interaction.followup.send(
                f"🗑️ Category removed: **{slug}**\n\nAll notification history has been deleted.",
                ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"⚠️ Category **{slug}** not found.\nUse `/category list` to see active categories.",
                ephemeral=True
            )
    
    @category_group.command(name="list", description="Show all active categories")
    async def category_list(self, interaction: discord.Interaction):
        categories = await self.bot.db.get_guild_categories(interaction.guild_id)
        
        if not categories:
            await interaction.response.send_message(
                "📭 No active categories in this server.\nUse `/category add` to create one!",
                ephemeral=True
            )
            return
        
        embed = self._build_category_list_embed(categories)
        
        for field in embed.fields:
            if 'bilety-lotnicze' in field.name.lower():
                field.name += " [PROTECTED]"
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @category_group.command(name="trigger", description="Manually trigger category notification")
    @app_commands.describe(slug="Category to trigger")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_trigger(self, interaction: discord.Interaction, slug: str):
        await interaction.response.defer(ephemeral=True)
        
        slug = slug.lower().strip()
        
        category = await self.bot.db.get_category_by_slug(interaction.guild_id, slug)
        if not category:
            await interaction.followup.send(
                f"⚠️ Category **{slug}** not found.",
                ephemeral=True
            )
            return
        
        await interaction.followup.send(
            f"⚡ Manual trigger started for: **{slug}**\nPlease wait...",
            ephemeral=True
        )
        
        await self.process_category_notification(category, manual_trigger=True, interaction=interaction)
    
    @category_group.command(name="pause", description="Pause a category")
    @app_commands.describe(slug="Category to pause")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_pause(self, interaction: discord.Interaction, slug: str):
        slug = slug.lower().strip()
        
        if slug == 'bilety-lotnicze':
            await interaction.response.send_message(
                "🔒 Cannot pause protected system category",
                ephemeral=True
            )
            return
        
        updated = await self.bot.db.update_category_status(interaction.guild_id, slug, 'paused')
        if updated:
            await interaction.response.send_message(
                f"⏸️ Category paused: **{slug}**\n\nUse `/category resume {slug}` to reactivate.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ Category **{slug}** not found.",
                ephemeral=True
            )
    
    @category_group.command(name="resume", description="Resume a paused category")
    @app_commands.describe(slug="Category to resume")
    @app_commands.checks.has_permissions(administrator=True)
    async def category_resume(self, interaction: discord.Interaction, slug: str):
        slug = slug.lower().strip()
        
        updated = await self.bot.db.update_category_status(interaction.guild_id, slug, 'active')
        if updated:
            category = await self.bot.db.get_category_by_slug(interaction.guild_id, slug)
            schedule_str = self.category_manager.format_schedule(category)
            
            await interaction.response.send_message(
                f"▶️ Category resumed: **{slug}**\n\nNext notification: {schedule_str}",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"⚠️ Category **{slug}** not found.",
                ephemeral=True
            )
    
    @category_group.command(name="preview", description="Preview deals before adding category")
    @app_commands.describe(slug="Category slug to preview")
    async def category_preview(self, interaction: discord.Interaction, slug: str):
        await interaction.response.defer(ephemeral=True)
        
        slug = slug.lower().strip()
        
        result = await self.scraper.get_group_deals(slug, limit=3)
        
        if not result['success']:
            await interaction.followup.send(
                f"❌ Category **{slug}** not found on Pepper.pl\n\nUse `/category browse` to find valid categories.",
                ephemeral=True
            )
            return
        
        deals = result['deals']
        if not deals:
            await interaction.followup.send(
                f"✅ Category found: **{slug}**\n\n📭 No deals currently available.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title=f"✅ Category Preview: {slug}",
            description=f"Latest {len(deals)} deals:",
            color=Config.COLOR_SUCCESS
        )
        
        for i, deal in enumerate(deals, 1):
            temp = deal.get('temperature', 0)
            icon = self.get_temperature_icon(temp)
            
            value = f"💰 {deal.get('price', '???')} | {icon} {temp}° | 🪐 {deal.get('merchant', 'Unknown')}"
            embed.add_field(
                name=f"{i}. {deal['title'][:60]}...",
                value=value,
                inline=False
            )
        
        embed.set_footer(text=f"Ready to add? Use: /category add {slug} ...")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @category_add.error
    async def category_add_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** or **Manage Server** permission to add categories.",
                ephemeral=True
            )
        else:
            logger.error(f"Error in category add: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "⚠️ An unexpected error occurred. Please try again.",
                    ephemeral=True
                )


async def setup(bot):
    await bot.add_cog(PepperCommands(bot))