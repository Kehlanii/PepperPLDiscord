import asyncio
import datetime
import json
import logging
from typing import Any
from urllib.parse import quote

import aiohttp
from selectolax.parser import HTMLParser

from .config import Config

logger = logging.getLogger("PepperBot.Scraper")


class PepperScraper:
    HEADERS = {
        "User-Agent": Config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl,en-US;q=0.7,en;q=0.3",
        "Referer": f"{Config.BASE_URL}/",
    }

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def search_deals(
        self, query: str, limit: int = 7, sort: str = "relevance",
    ) -> dict[str, Any]:
        sort_param = f"&sort={sort}" if sort in ("new", "hot") else ""
        url = f"{Config.BASE_URL}/search?q={quote(query)}{sort_param}"
        return await self._fetch_and_parse(url, limit, context=f"search:{query}")

    async def get_hot_deals(self, limit: int = 7) -> dict[str, Any]:
        return await self._fetch_and_parse(Config.BASE_URL, limit, context="hot")

    async def get_group_deals(self, slug: str, limit: int = 7) -> dict[str, Any]:
        url = Config.GROUP_URL_TEMPLATE.format(slug)
        return await self._fetch_and_parse(url, limit, context=f"group:{slug}")

    async def get_flight_deals(self, limit: int = 10) -> dict[str, Any]:
        return await self._fetch_and_parse(
            Config.FLIGHT_CATEGORY_URL, limit, context="flights",
        )

    async def _fetch_and_parse(
        self, url: str, limit: int, context: str, retries: int = 3,
    ) -> dict[str, Any]:
        for attempt in range(retries):
            try:
                async with self.session.get(url, headers=self.HEADERS) as resp:
                    if resp.status != 200:
                        logger.warning("HTTP %d for %s", resp.status, url)
                        if resp.status in (429, 500, 502, 503, 504):
                            await asyncio.sleep(2 * (attempt + 1))
                            continue
                        return {"success": False, "error": f"HTTP {resp.status}", "deals": []}

                    html = await resp.text()
                    deals = self._extract_deals(html)
                    return {"success": True, "deals": deals[:limit], "total": len(deals)}

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning("Network error (%s): %s", context, e)
                if attempt == retries - 1:
                    return {"success": False, "error": str(e), "deals": []}
                await asyncio.sleep(2 * (attempt + 1))
            except Exception as e:
                logger.error("Unexpected error (%s): %s", context, e, exc_info=True)
                return {"success": False, "error": str(e), "deals": []}

        return {"success": False, "error": "Max retries exceeded", "deals": []}

    def _extract_deals(self, html: str) -> list[dict[str, Any]]:
        deals: list[dict[str, Any]] = []
        try:
            tree = HTMLParser(html)

            # Primary: Vue3 JSON data embedded in DOM
            for el in tree.css("[data-vue3]"):
                data_str = el.attributes.get("data-vue3", "")
                if "ThreadMainListItemNormalizer" not in data_str:
                    continue
                try:
                    vue = json.loads(data_str)
                    thread = vue.get("props", {}).get("thread")
                    if thread:
                        deal = self._parse_thread(thread)
                        if deal:
                            deals.append(deal)
                except json.JSONDecodeError:
                    continue

            if deals:
                return deals

            # Fallback: raw HTML article scraping
            for article in tree.css("article.thread"):
                deal = self._parse_article(article)
                if deal:
                    deals.append(deal)

        except Exception as e:
            logger.error("Deal extraction failed: %s", e, exc_info=True)

        return deals

    def _parse_thread(self, thread: dict) -> dict[str, Any] | None:
        """Parse Vue3 thread JSON into normalized deal dict."""
        try:
            if thread.get("isExpired") or thread.get("isArchived"):
                return None
            status = thread.get("status", "unknown")
            if status in ("expired", "archived", "deleted"):
                return None

            title = thread.get("title", "Brak tytułu")
            thread_id = thread.get("threadId", "")
            slug = thread.get("titleSlug", "")
            link = (
                f"{Config.BASE_URL}/promocje/{slug}-{thread_id}"
                if slug and thread_id
                else thread.get("shareableLink", "")
            )

            price = thread.get("price")
            next_best = thread.get("nextBestPrice")

            try:
                temp = int(float(thread.get("temperature", 0)))
            except (ValueError, TypeError):
                temp = 0

            merchant_data = thread.get("merchant", {})
            merchant = (
                merchant_data.get("merchantName", "Nieznany")
                if isinstance(merchant_data, dict)
                else "Nieznany"
            )

            image_url = None
            img = thread.get("mainImage", {})
            if isinstance(img, dict) and img.get("path") and img.get("name"):
                ext = img.get("ext", "jpg")
                image_url = (
                    f"https://static.pepper.pl/{img['path']}/{img['name']}"
                    f"/re/600x600/qt/80/{img['name']}.{ext}"
                )

            posted_timestamp = None
            if published := thread.get("publishedAt"):
                try:
                    posted_timestamp = datetime.datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    pass

            return {
                "title": title,
                "link": link,
                "price": f"{price} zł" if price else None,
                "next_best_price": f"{next_best} zł" if next_best else None,
                "temperature": temp,
                "merchant": merchant,
                "image_url": image_url,
                "voucher_code": thread.get("voucherCode", ""),
                "posted_timestamp": posted_timestamp,
                "status": status,
            }
        except Exception as e:
            logger.error("Thread parse error: %s", e, exc_info=True)
            return None

    def _parse_article(self, article) -> dict[str, Any] | None:
        """Fallback HTML scraper for article.thread elements."""
        try:
            title_el = article.css_first(".thread-title a")
            if not title_el:
                return None

            title = title_el.text(strip=True)
            link = title_el.attributes.get("href", "")
            if link and not link.startswith("http"):
                link = f"{Config.BASE_URL}{link}"

            price_el = article.css_first(".thread-price")
            price = price_el.text(strip=True) if price_el else None

            temp_el = article.css_first(".vote-temp")
            try:
                temp = int(temp_el.text(strip=True).replace("°", "")) if temp_el else 0
            except ValueError:
                temp = 0

            merchant_el = article.css_first(".thread-card-merchant")
            merchant = merchant_el.text(strip=True) if merchant_el else "Nieznany"

            img_el = article.css_first("img.thread-image")
            image_url = img_el.attributes.get("src") if img_el else None

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
            logger.debug("Article parse error: %s", e)
            return None
