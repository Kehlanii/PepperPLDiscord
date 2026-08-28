from typing import Any

import discord

from .config import Config
from .embeds import temperature_icon


class DealPaginator(discord.ui.View):
    def __init__(self, deals: list[dict[str, Any]], author: discord.User):
        super().__init__(timeout=180)
        self.deals = deals
        self.author = author
        self.page = 0
        self.total = len(deals)

        self.btn_prev = discord.ui.Button(label="⬅️", style=discord.ButtonStyle.secondary)
        self.btn_prev.callback = self._on_prev

        self.btn_next = discord.ui.Button(label="➡️", style=discord.ButtonStyle.primary)
        self.btn_next.callback = self._on_next

        self.btn_close = discord.ui.Button(label="🗑️", style=discord.ButtonStyle.danger)
        self.btn_close.callback = self._on_close

        self._rebuild()

    def _embed(self) -> discord.Embed:
        deal = self.deals[self.page]
        embed = discord.Embed(
            title=deal["title"][:250],
            url=deal["link"] or None,
            color=Config.COLOR_PRIMARY,
        )

        price_text = (
            "Darmowa" if deal.get("price") == "0 zł"
            else (deal["price"] or "---")
        )
        if deal.get("next_best_price"):
            price_text += f"  ~~{deal['next_best_price']}~~"

        embed.add_field(name="💰 Cena", value=f"**{price_text}**", inline=True)
        embed.add_field(name="🏪 Sklep", value=deal["merchant"], inline=True)

        temp = deal["temperature"]
        icon = temperature_icon(temp)
        embed.add_field(name=f"{icon} Ocena", value=f"{temp}°", inline=True)

        if deal.get("voucher_code"):
            embed.add_field(
                name="🎫 Kod",
                value=f"```\n{deal['voucher_code']}\n```",
                inline=False,
            )

        if deal.get("image_url"):
            embed.set_thumbnail(url=deal["image_url"])

        embed.set_footer(
            text=f"Okazja {self.page + 1} z {self.total} • Pepper.pl",
            icon_url="https://static.pepper.pl/assets/img/favicons/favicon-32x32.png",
        )
        return embed

    def _rebuild(self) -> None:
        self.clear_items()
        self.btn_prev.disabled = self.page == 0
        self.btn_next.disabled = self.page >= self.total - 1
        self.add_item(self.btn_prev)
        self.add_item(self.btn_next)
        self.add_item(self.btn_close)

        url = self.deals[self.page].get("link")
        if url:
            self.add_item(discord.ui.Button(
                label="🔗 Idź do okazji",
                style=discord.ButtonStyle.link,
                url=url,
            ))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "🚫 To nie jest twoje menu.", ephemeral=True,
            )
            return False
        return True

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        self.page -= 1
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        self._rebuild()
        await interaction.response.edit_message(embed=self._embed(), view=self)

    async def _on_close(self, interaction: discord.Interaction) -> None:
        await interaction.message.delete()

    def get_initial_embed(self) -> discord.Embed:
        return self._embed()
