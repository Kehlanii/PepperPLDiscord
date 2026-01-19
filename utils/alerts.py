import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional

from .db import Database

logger = logging.getLogger("PepperBot.Alerts")

FRESHNESS_CUTOFF_HOURS = 24
MIN_TEMPERATURE = 50
MAX_REASONABLE_PRICE = 1000000


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
        notifications = []
        batch_seen = []
        seen_in_cycle = set()

        current_time = datetime.datetime.now()
        freshness_cutoff = current_time - datetime.timedelta(hours=FRESHNESS_CUTOFF_HOURS)

        unique_queries = await self.db.get_all_unique_queries()
        logger.info(f"Checking {len(unique_queries)} unique queries...")

        for query in unique_queries:
            result = await scraper.search_deals(query, limit=5, sort="new")

            if not result["success"]:
                continue

            subscribers = await self.db.get_alerts_by_query(query)

            if not subscribers:
                continue

            for deal in result["deals"]:
                posted_time = deal.get("posted_timestamp")
                
                if posted_time:
                    if isinstance(posted_time, str):
                        try:
                            posted_time = datetime.datetime.fromisoformat(posted_time.replace('Z', '+00:00'))
                        except ValueError:
                            posted_time = None
                    
                    if posted_time and posted_time < freshness_cutoff:
                        logger.debug(f"Skipping old deal: {deal['link']}")
                        continue
                
                temp = deal.get('temperature', 0)
                if temp < MIN_TEMPERATURE:
                    logger.debug(f"Skipping low-quality deal: {temp}° - {deal['link']}")
                    continue

                deal_id = deal["link"]
                deal_price = self._parse_price(deal["price"])

                if deal_price is None:
                    logger.warning(f"Skipping deal with invalid price: {deal['link']}")
                    continue
                
                if deal_price > MAX_REASONABLE_PRICE:
                    logger.warning(f"Skipping deal with unreasonable price: {deal_price} zł")
                    continue

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
                        if deal_price > 0 and deal_price > max_price:
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

    def _parse_price(self, price_str: Optional[str]) -> Optional[float]:
        if not price_str:
            return None
        try:
            clean = price_str.lower().replace("zł", "").replace(" ", "").replace(",", ".")

            if "darm" in clean or "free" in clean or "bezpłatn" in clean:
                return 0.0

            return float(clean)
        except ValueError:
            logger.warning(f"Failed to parse price: {price_str}")
            return None