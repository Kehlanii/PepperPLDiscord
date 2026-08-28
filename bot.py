import logging
import os

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

from utils.db import Database
from utils.scraper import PepperScraper

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("PepperBot")

COGS = [
    "cogs.search",
    "cogs.alerts",
    "cogs.categories",
    "cogs.flights",
    "cogs.text_commands",
]


class PepperBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)
        self.session: aiohttp.ClientSession | None = None
        self.db = Database()
        self.scraper: PepperScraper | None = None

    async def setup_hook(self) -> None:
        connector = aiohttp.TCPConnector(
            limit=50,
            limit_per_host=10,
            ttl_dns_cache=600,
            enable_cleanup_closed=True,
            force_close=False,
        )
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=15, connect=5, sock_read=10),
            headers={"Connection": "keep-alive", "Accept-Encoding": "gzip, deflate"},
        )

        # Single scraper for all cogs — no more @property re-instantiation
        self.scraper = PepperScraper(self.session)

        await self.db.init()

        for cog in COGS:
            try:
                await self.load_extension(cog)
                logger.info("Loaded: %s", cog)
            except Exception as e:
                logger.error("Failed to load %s: %s", cog, e, exc_info=True)

        synced = await self.tree.sync()
        logger.info("Synced %d command(s)", len(synced))

    async def close(self) -> None:
        if self.session:
            await self.session.close()
        await self.db.close()
        await super().close()

    async def on_ready(self) -> None:
        logger.info("%s connected (%d guilds)", self.user, len(self.guilds))
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="Pepper.pl",
            ),
        )

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError,
    ) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="❌ Missing Argument",
                description=f"Usage: `{ctx.prefix}{ctx.command} {ctx.command.signature}`",
                color=0xFF0000,
            )
            await ctx.send(embed=embed)
        else:
            logger.error(
                "Command error in %s: %s", ctx.command, error, exc_info=True,
            )


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        logger.error("DISCORD_BOT_TOKEN not found in .env!")
        return

    bot = PepperBot()
    try:
        bot.run(token)
    except discord.LoginFailure:
        logger.error("Invalid Discord token!")
    except Exception as e:
        logger.error("Failed to start: %s", e, exc_info=True)


if __name__ == "__main__":
    main()
