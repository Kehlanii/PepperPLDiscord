import datetime
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PepperBot.DealFilter")

FRESHNESS_CUTOFF_HOURS = 24
MIN_TEMPERATURE = 50
MAX_REASONABLE_PRICE = 1000000


class DealFilter: 
    @staticmethod
    def filter_deals(
        deals: List[Dict[str, Any]],
        *,
        check_freshness: bool = True,
        check_temperature: bool = True,
        check_price: bool = True,
        min_temperature: Optional[int] = None,
        max_price: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        if not deals:
            return []
        
        filtered = []
        current_time = datetime.datetime.now()
        freshness_cutoff = current_time - datetime.timedelta(hours=FRESHNESS_CUTOFF_HOURS)
        min_temp_threshold = min_temperature if min_temperature is not None else MIN_TEMPERATURE
        
        for deal in deals:
            if check_freshness:
                posted_time = deal.get("posted_timestamp")
                
                if posted_time:
                    if isinstance(posted_time, str):
                        try:
                            posted_time = datetime.datetime.fromisoformat(
                                posted_time.replace('Z', '+00:00')
                            )
                        except ValueError:
                            posted_time = None
                    
                    if posted_time and posted_time < freshness_cutoff:
                        logger.debug(
                            f"Skipping old deal (posted {posted_time}): "
                            f"{deal.get('link', 'unknown')}"
                        )
                        continue
            
            if check_temperature:
                temp = deal.get('temperature', 0)
                if temp < min_temp_threshold:
                    logger.debug(
                        f"Skipping low-temperature deal ({temp}°): "
                        f"{deal.get('link', 'unknown')}"
                    )
                    continue
            
            if check_price:
                deal_price = DealFilter._parse_price(deal.get("price"))
                
                if deal_price is None:
                    logger.warning(
                        f"Skipping deal with invalid price: {deal.get('link', 'unknown')}"
                    )
                    continue
                
                if deal_price > MAX_REASONABLE_PRICE:
                    logger.warning(
                        f"Skipping deal with unreasonable price ({deal_price} zł): "
                        f"{deal.get('link', 'unknown')}"
                    )
                    continue
                
                if max_price is not None and deal_price > 0 and deal_price > max_price:
                    logger.debug(
                        f"Skipping deal above max price ({deal_price} > {max_price}): "
                        f"{deal.get('link', 'unknown')}"
                    )
                    continue
            
            filtered.append(deal)
        
        filtered_count = len(deals) - len(filtered)
        if filtered_count > 0:
            logger.info(
                f"Filtered {filtered_count}/{len(deals)} deals "
                f"({len(filtered)} quality deals remain)"
            )
        
        return filtered
    
    @staticmethod
    def _parse_price(price_str: Optional[str]) -> Optional[float]:
        if not price_str:
            return None
        
        try:
            clean = price_str.lower().replace("zł", "").replace(" ", "").replace(",", ".")
            if any(keyword in clean for keyword in ["darm", "free", "bezpłatn"]):
                return 0.0  
            return float(clean)
        
        except ValueError:
            logger.warning(f"Failed to parse price: {price_str}")
            return None
    
    @staticmethod
    def get_filter_summary(
        original_count: int,
        filtered_count: int,
        check_freshness: bool,
        check_temperature: bool,
        check_price: bool
    ) -> str:
        if filtered_count == original_count:
            return f"✅ All {original_count} deals meet quality standards"
        
        removed = original_count - filtered_count
        filters_applied = []
        
        if check_freshness:
            filters_applied.append("freshness (<24h)")
        if check_temperature:
            filters_applied.append(f"temperature (≥{MIN_TEMPERATURE}°)")
        if check_price:
            filters_applied.append("price validation")
        
        filters_str = ", ".join(filters_applied)
        
        return (
            f"🔍 Found {original_count} deals, showing {filtered_count} quality deals\n"
            f"Filters applied: {filters_str}"
        )