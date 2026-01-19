import asyncio
import datetime
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import aiohttp
from selectolax.parser import HTMLParser

logger = logging.getLogger("PepperBot.Scraper")


class PepperScraper:

    BASE_URL = "https://www.pepper.pl"
    DEFAULT_HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        "Referer": "https://www.pepper.pl/",
    }

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def search_deals(
        self, query: str, limit: int = 7, sort: str = "relevance"
    ) -> Dict[str, Any]:
        sort_param = ""
        if sort == "new":
            sort_param = "&sort=new"
        elif sort == "hot":
            sort_param = "&sort=hot"

        search_url = f"{self.BASE_URL}/search?q={quote(query)}{sort_param}"
        return await self._fetch_and_parse(search_url, limit, context=f"search: {query} ({sort})")

    async def get_hot_deals(self, limit: int = 7) -> Dict[str, Any]:
        return await self._fetch_and_parse(self.BASE_URL, limit, context="hot deals")

    async def get_group_deals(self, group_slug: str, limit: int = 7) -> Dict[str, Any]:
        from .config import Config

        url = Config.GROUP_URL_TEMPLATE.format(group_slug)
        return await self._fetch_and_parse(url, limit, context=f"group: {group_slug}")

    async def get_flight_deals(self, limit: int = 10) -> Dict[str, Any]:
        from .config import Config

        return await self._fetch_and_parse(
            Config.FLIGHT_CATEGORY_URL, limit, context="flight deals"
        )

    async def _fetch_and_parse(
        self, url: str, limit: int, context: str, retries: int = 3
    ) -> Dict[str, Any]:
        for attempt in range(retries):
            try:
                logger.info(f"Fetching {context} from: {url} (Attempt {attempt + 1}/{retries})")
                async with self.session.get(
                    url, headers=self.DEFAULT_HEADERS, timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"HTTP {response.status} for {url}")
                        if response.status in [429, 500, 502, 503, 504]:
                            await asyncio.sleep(2 * (attempt + 1))
                            continue
                        return {"success": False, "error": f"HTTP {response.status}", "deals": []}

                    html = await response.text()
                    
                    deals = self._extract_deals_from_html(html)
                    
                    return {"success": True, "deals": deals[:limit], "total": len(deals)}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Network error fetching {context}: {e}")
                if attempt == retries - 1:
                    logger.error(f"Failed to fetch {context} after {retries} attempts", exc_info=True)
                    return {"success": False, "error": str(e), "deals": []}
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                logger.error(f"Unexpected error fetching {context}: {e}", exc_info=True)
                return {"success": False, "error": str(e), "deals": []}

        return {"success": False, "error": "Max retries exceeded", "deals": []}

    def _extract_deals_from_html(self, html: str) -> List[Dict[str, Any]]:
        deals = []
        try:
            tree = HTMLParser(html)
            
            vue_elements = tree.css('[data-vue3]')
            for element in vue_elements:
                data_str = element.attributes.get('data-vue3', '')
                if "ThreadMainListItemNormalizer" not in data_str:
                    continue
                try:
                    vue_data = json.loads(data_str)
                    if "props" in vue_data and "thread" in vue_data["props"]:
                        thread = vue_data["props"]["thread"]
                        deal = self._parse_thread_data(thread)
                        if deal:
                            deals.append(deal)
                except json.JSONDecodeError:
                    continue

            if deals:
                logger.info(f"Extracted {len(deals)} deals using Vue method (selectolax)")
                return deals

            logger.info("Vue extraction yielded 0 deals. Trying HTML fallback...")
            articles = tree.css('article.thread')
            for article in articles:
                deal = self._parse_article_html_selectolax(article)
                if deal:
                    deals.append(deal)

            return deals

        except Exception as e:
            logger.error(f"Error extracting deals: {e}", exc_info=True)
            return deals

    def _parse_article_html_selectolax(self, article) -> Optional[Dict[str, Any]]:
        try:
            title_elem = article.css_first('.thread-title a')
            if not title_elem:
                return None

            title = title_elem.text(strip=True)
            link = title_elem.attributes.get('href', '')
            if link and not link.startswith('http'):
                link = f"{self.BASE_URL}{link}"

            price_elem = article.css_first('.thread-price')
            price = price_elem.text(strip=True) if price_elem else None

            temp_elem = article.css_first('.vote-temp')
            temp_str = temp_elem.text(strip=True).replace('°', '') if temp_elem else '0'
            try:
                temp = int(temp_str)
            except:
                temp = 0

            merchant_elem = article.css_first('.thread-card-merchant')
            merchant = merchant_elem.text(strip=True) if merchant_elem else "Nieznany"

            img_elem = article.css_first('img.thread-image')
            image_url = img_elem.attributes.get('src') if img_elem else None

            return {
                "title": title,
                "link": link,
                "price": price,
                "next_best_price": None,
                "temperature": temp,
                "merchant": merchant,
                "image_url": image_url,
                "voucher_code": None,
                "posted_timestamp": None,
                "status": "unknown",
            }
        except Exception as e:
            logger.debug(f"Selectolax parsing error: {e}")
            return None

    def _parse_thread_data(self, thread: Dict) -> Optional[Dict]:
        try:
            status = thread.get("status", "unknown")
            is_expired = thread.get("isExpired", False)
            is_archived = thread.get("isArchived", False)
            
            if is_expired or is_archived or status in ["expired", "archived", "deleted"]:
                logger.debug(f"Skipping unavailable deal: status={status}, expired={is_expired}")
                return None
            
            title = thread.get("title", "Brak tytułu")
            thread_id = thread.get("threadId", "")
            title_slug = thread.get("titleSlug", "")

            if title_slug and thread_id:
                link = f"{self.BASE_URL}/promocje/{title_slug}-{thread_id}"
            else:
                link = thread.get("shareableLink", "")

            price = thread.get("price")
            price_str = f"{price} zł" if price else None

            next_best = thread.get("nextBestPrice")
            next_best_str = f"{next_best} zł" if next_best else None

            temp = thread.get("temperature", 0)
            try:
                temp = float(temp) if isinstance(temp, (int, float, str)) else 0
            except ValueError:
                temp = 0

            merchant_data = thread.get("merchant", {})
            merchant = (
                merchant_data.get("merchantName", "Nieznany")
                if isinstance(merchant_data, dict)
                else "Nieznany"
            )

            image_url = None
            main_image = thread.get("mainImage", {})
            if isinstance(main_image, dict):
                path = main_image.get("path")
                name = main_image.get("name")
                ext = main_image.get("ext")
                if path and name:
                    image_url = (
                        f"https://static.pepper.pl/{path}/{name}/re/600x600/qt/80/{name}.{ext}"
                    )

            published_at = thread.get("publishedAt")
            posted_timestamp = None
            if published_at:
                try:
                    posted_timestamp = datetime.datetime.fromisoformat(published_at.replace('Z', '+00:00'))
                except (ValueError, AttributeError):
                    posted_timestamp = None

            return {
                "title": title,
                "link": link,
                "price": price_str,
                "next_best_price": next_best_str,
                "temperature": int(temp),
                "merchant": merchant,
                "image_url": image_url,
                "voucher_code": thread.get("voucherCode", ""),
                "posted_timestamp": posted_timestamp,
                "status": status,
            }
        except Exception as e:
            logger.error(f"Error parsing thread data: {e}", exc_info=True)
            return None