import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from .db import Database

logger = logging.getLogger("PepperBot.Alerts")


class AlertsManager:
    def __init__(self, db: Database):
        self.db = db

    async def load_alerts(self):
        pass

    async def add_alert(self, user_id: int, query: str, max_price: Optional[float] = None) -> bool:
        return await self.db.add_alert(user_id, query, max_price)

    async def remove_alert(self, user_id: int, query: str) -> bool:
        return await self.db.remove_alert(user_id, query)

    async def get_alerts(self, user_id: int) -> List[Dict[str, Any]]:
        return await self.db.get_user_alerts(user_id)

    async def check_alerts(self, scraper) -> List[Dict[str, Any]]:
        from utils.deal_filter import DealFilter
        
        notifications = []
        batch_seen = []
        seen_in_cycle = set()

        unique_queries = await self.db.get_all_unique_queries()
        logger.info(f"Checking {len(unique_queries)} unique queries...")

        for query in unique_queries:
            result = await scraper.search_deals(query, limit=5, sort="new")

            if not result["success"]:
                continue

            subscribers = await self.db.get_alerts_by_query(query)

            if not subscribers:
                continue

            all_deals = result["deals"]
            filtered_deals = DealFilter.filter_deals(
                all_deals,
                check_freshness=True,
                check_temperature=True,
                check_price=True
            )

            for deal in filtered_deals:
                deal_id = deal["link"]

                for sub in subscribers:
                    user_id = sub["user_id"]
                    max_price = sub["max_price"]
                    alert_id = sub["id"]

                    cache_key = (alert_id, deal_id)
                    if cache_key in seen_in_cycle:
                        continue

                    if await self.db.is_deal_seen_by_alert(alert_id, deal_id):
                        seen_in_cycle.add(cache_key)
                        continue

                    if max_price is not None:
                        deal_price = DealFilter._parse_price(deal.get("price"))
                        if deal_price and deal_price > 0 and deal_price > max_price:
                            continue

                    notifications.append({
                        "user_id": user_id,
                        "deal": deal,
                        "query": query
                    })
                    batch_seen.append(cache_key)
                    seen_in_cycle.add(cache_key)

            if len(unique_queries) > 5:
                await asyncio.sleep(1.5)

        if batch_seen:
            await self.db.mark_deals_seen_batch(batch_seen)
            logger.info(f"Batch marked {len(batch_seen)} deals as seen")

        logger.info(f"Alert check complete: {len(notifications)} notifications, {len(seen_in_cycle)} cached checks")
        return notifications