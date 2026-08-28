import datetime
import logging
from typing import Any

from .pricing import parse_price

logger = logging.getLogger("PepperBot.DealFilter")

FRESHNESS_HOURS = 24
DEFAULT_MIN_TEMP = 50
MAX_SANE_PRICE = 1_000_000


class DealFilter:
    @staticmethod
    def filter_deals(
        deals: list[dict[str, Any]],
        *,
        check_freshness: bool = True,
        check_temperature: bool = True,
        check_price: bool = True,
        min_temperature: int | None = None,
        max_price: float | None = None,
    ) -> list[dict[str, Any]]:
        if not deals:
            return []

        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(hours=FRESHNESS_HOURS)
        min_temp = min_temperature if min_temperature is not None else DEFAULT_MIN_TEMP
        filtered = []

        for deal in deals:
            if check_freshness:
                ts = deal.get("posted_timestamp")
                if ts:
                    if isinstance(ts, str):
                        try:
                            ts = datetime.datetime.fromisoformat(
                                ts.replace("Z", "+00:00")
                            )
                        except ValueError:
                            ts = None
                    # Normalize naive timestamps to UTC
                    if ts and not ts.tzinfo:
                        ts = ts.replace(tzinfo=datetime.timezone.utc)
                    if ts and ts < cutoff:
                        continue

            if check_temperature and deal.get("temperature", 0) < min_temp:
                continue

            if check_price:
                price = parse_price(deal.get("price"))
                if price is None:
                    continue
                if price > MAX_SANE_PRICE:
                    continue
                if max_price is not None and price > 0 and price > max_price:
                    continue

            filtered.append(deal)

        dropped = len(deals) - len(filtered)
        if dropped:
            logger.info(
                "Filtered %d/%d deals (%d remain)", dropped, len(deals), len(filtered),
            )
        return filtered
