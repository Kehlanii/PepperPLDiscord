import asyncio
import logging
from typing import Any

from .db import Database
from .deal_filter import DealFilter
from .pricing import parse_price

logger = logging.getLogger("PepperBot.Alerts")

MAX_ALERTS_PER_USER = 10


class AlertsManager:
    """Owns full alert lifecycle: CRUD, validation, polling."""

    def __init__(self, db: Database):
        self.db = db

    async def add_alert(
        self, user_id: int, query: str, max_price: float | None = None,
    ) -> tuple[bool, str]:
        """Add alert with limit enforcement. Returns (success, user-facing message)."""
        current = await self.db.get_user_alerts(user_id)
        if len(current) >= MAX_ALERTS_PER_USER:
            return False, f"❌ Max {MAX_ALERTS_PER_USER} alerts. Remove some first."

        added = await self.db.add_alert(user_id, query, max_price)
        if not added:
            return False, "⚠️ Error adding alert."

        msg = f"✅ Watching: **{query}**"
        if max_price:
            msg += f" (< {max_price} zł)"
        msg += "\n🔔 Checking every 15 minutes"
        return True, msg

    async def remove_alert(self, user_id: int, query: str) -> tuple[bool, str]:
        """Remove alert. Returns (success, user-facing message)."""
        removed = await self.db.remove_alert(user_id, query)
        if removed:
            return True, f"🗑️ Stopped watching: **{query}**"
        return False, f"⚠️ Alert **{query}** not found.\nUse `p alerts` to see your list."

    async def get_alerts(self, user_id: int) -> list[dict[str, Any]]:
        return await self.db.get_user_alerts(user_id)

    async def check_alerts(self, scraper) -> list[dict[str, Any]]:
        """Poll all unique queries, return notifications for new matching deals."""
        notifications: list[dict[str, Any]] = []
        batch_seen: list[tuple] = []
        seen_in_cycle: set[tuple] = set()

        queries = await self.db.get_all_unique_queries()
        logger.info("Checking %d unique alert queries", len(queries))

        for query in queries:
            result = await scraper.search_deals(query, limit=5, sort="new")
            if not result["success"]:
                continue

            subscribers = await self.db.get_alerts_by_query(query)
            if not subscribers:
                continue

            deals = DealFilter.filter_deals(
                result["deals"],
                check_freshness=True,
                check_temperature=True,
                check_price=True,
            )

            for deal in deals:
                deal_id = deal["link"]
                for sub in subscribers:
                    alert_id = sub["id"]
                    cache_key = (alert_id, deal_id)
                    if cache_key in seen_in_cycle:
                        continue

                    if await self.db.is_deal_seen_by_alert(alert_id, deal_id):
                        seen_in_cycle.add(cache_key)
                        continue

                    # Per-subscriber price cap
                    if sub["max_price"] is not None:
                        deal_price = parse_price(deal.get("price"))
                        if deal_price is None:
                            continue  # unknown price, user set cap — skip
                        if deal_price > 0 and deal_price > sub["max_price"]:
                            continue

                    notifications.append({
                        "user_id": sub["user_id"],
                        "deal": deal,
                        "query": query,
                    })
                    batch_seen.append(cache_key)
                    seen_in_cycle.add(cache_key)

            # Rate limit between scraper requests
            if len(queries) > 5:
                await asyncio.sleep(1.5)

        if batch_seen:
            await self.db.mark_deals_seen_batch(batch_seen)

        logger.info(
            "Alert check: %d notifications, %d cached",
            len(notifications), len(seen_in_cycle) - len(batch_seen),
        )
        return notifications
