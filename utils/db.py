import logging
import os
from typing import Any

import aiosqlite

logger = logging.getLogger("PepperBot.Database")


class Database:
    def __init__(self, db_path: str = "pepperbot.db"):
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()
        await self._run_migrations()
        logger.info("Database initialized (WAL mode, persistent connection)")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if not self._conn:
            raise RuntimeError("Database not initialized — call init() first")
        return self._conn


    async def _create_tables(self) -> None:
        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS sent_deals (
                deal_id TEXT PRIMARY KEY,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                query TEXT NOT NULL,
                max_price REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, query)
            );
            CREATE TABLE IF NOT EXISTS alert_history (
                alert_id INTEGER,
                deal_id TEXT,
                seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(alert_id) REFERENCES alerts(id) ON DELETE CASCADE,
                PRIMARY KEY(alert_id, deal_id)
            );
            CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON alerts(user_id);
            CREATE INDEX IF NOT EXISTS idx_alerts_query ON alerts(query);
            CREATE INDEX IF NOT EXISTS idx_alert_history_lookup
                ON alert_history(alert_id, deal_id);
            CREATE INDEX IF NOT EXISTS idx_sent_deals_sent_at ON sent_deals(sent_at);
        """)

    async def _run_migrations(self) -> None:
        async with self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='category_configs'"
        ) as cur:
            if await cur.fetchone():
                return

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base_dir, "migrations", "migration_001_category_system.sql")
        if not os.path.exists(path):
            raise FileNotFoundError(f"Migration not found: {path}")

        with open(path, encoding="utf-8") as f:
            await self.conn.executescript(f.read())
        logger.info("Applied migration: category_system")

    # Sent deals (flight dedup)

    async def is_deal_sent(self, deal_id: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM sent_deals WHERE deal_id = ?", (deal_id,)
        ) as cur:
            return await cur.fetchone() is not None

    async def add_sent_deal(self, deal_id: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO sent_deals (deal_id) VALUES (?)", (deal_id,)
        )
        await self.conn.commit()

    async def cleanup_old_deals(self, days: int = 30) -> int:
        cur = await self.conn.execute(
            "DELETE FROM sent_deals WHERE sent_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await self.conn.commit()
        return cur.rowcount

    # Alerts

    async def add_alert(
        self, user_id: int, query: str, max_price: float | None = None,
    ) -> bool:
        try:
            await self.conn.execute(
                """INSERT INTO alerts (user_id, query, max_price) VALUES (?, ?, ?)
                   ON CONFLICT(user_id, query) DO UPDATE SET
                       max_price = excluded.max_price""",
                (user_id, query, max_price),
            )
            await self.conn.commit()
            return True
        except Exception as e:
            logger.error("Error adding alert: %s", e)
            return False

    async def remove_alert(self, user_id: int, query: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM alerts WHERE user_id = ? AND query = ?", (user_id, query),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_user_alerts(self, user_id: int) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM alerts WHERE user_id = ?", (user_id,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def get_all_unique_queries(self) -> list[str]:
        async with self.conn.execute("SELECT DISTINCT query FROM alerts") as cur:
            return [row[0] for row in await cur.fetchall()]

    async def get_alerts_by_query(self, query: str) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM alerts WHERE query = ?", (query,)
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def is_deal_seen_by_alert(self, alert_id: int, deal_id: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM alert_history WHERE alert_id = ? AND deal_id = ?",
            (alert_id, deal_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_deals_seen_batch(self, records: list[tuple]) -> None:
        if not records:
            return
        await self.conn.executemany(
            "INSERT OR IGNORE INTO alert_history (alert_id, deal_id) VALUES (?, ?)",
            records,
        )
        await self.conn.commit()

    # Category configs

    async def add_category_config(
        self, guild_id: int, slug: str, channel_id: int,
        schedule_type: str, schedule_time: str,
        schedule_day: str | None = None, schedule_date: int | None = None,
        name: str | None = None, min_temperature: int = 0,
        max_price: float | None = None,
    ) -> int | None:
        try:
            cur = await self.conn.execute(
                """INSERT INTO category_configs
                   (guild_id, slug, name, channel_id, schedule_type, schedule_time,
                    schedule_day, schedule_date, min_temperature, max_price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (guild_id, slug, name, channel_id, schedule_type, schedule_time,
                 schedule_day, schedule_date, min_temperature, max_price),
            )
            await self.conn.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            logger.warning("Category %s already exists for guild %d", slug, guild_id)
            return None
        except Exception as e:
            logger.error("Error adding category: %s", e)
            return None

    async def remove_category_config(self, guild_id: int, slug: str) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM category_configs WHERE guild_id = ? AND slug = ?",
            (guild_id, slug),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_guild_categories(
        self, guild_id: int, status: str | None = None,
    ) -> list[dict[str, Any]]:
        if status:
            sql = "SELECT * FROM category_configs WHERE guild_id = ? AND status = ?"
            params: tuple = (guild_id, status)
        else:
            sql = "SELECT * FROM category_configs WHERE guild_id = ?"
            params = (guild_id,)
        async with self.conn.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def get_category_by_slug(
        self, guild_id: int, slug: str,
    ) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM category_configs WHERE guild_id = ? AND slug = ?",
            (guild_id, slug),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def update_category_status(
        self, guild_id: int, slug: str, status: str,
    ) -> bool:
        cur = await self.conn.execute(
            """UPDATE category_configs SET status = ?, updated_at = CURRENT_TIMESTAMP
               WHERE guild_id = ? AND slug = ?""",
            (status, guild_id, slug),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def update_category_last_run(self, category_id: int) -> None:
        await self.conn.execute(
            "UPDATE category_configs SET last_run = CURRENT_TIMESTAMP WHERE id = ?",
            (category_id,),
        )
        await self.conn.commit()

    async def get_active_categories(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM category_configs WHERE status = 'active' ORDER BY guild_id, id"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]

    async def is_category_deal_sent(self, category_id: int, deal_id: str) -> bool:
        async with self.conn.execute(
            "SELECT 1 FROM category_sent_deals WHERE category_id = ? AND deal_id = ?",
            (category_id, deal_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def mark_category_deals_sent_batch(self, records: list[tuple]) -> None:
        if not records:
            return
        await self.conn.executemany(
            "INSERT OR IGNORE INTO category_sent_deals (category_id, deal_id) VALUES (?, ?)",
            records,
        )
        await self.conn.commit()

    async def cleanup_category_deals(self, days: int = 30) -> int:
        cur = await self.conn.execute(
            "DELETE FROM category_sent_deals WHERE sent_at < datetime('now', ?)",
            (f"-{days} days",),
        )
        await self.conn.commit()
        return cur.rowcount

    async def update_category_stats(
        self, category_id: int, deals_found: int, deals_sent: int, errors: int = 0,
    ) -> None:
        await self.conn.execute(
            """INSERT INTO category_stats
                   (category_id, date, deals_found, deals_sent, scrape_errors)
               VALUES (?, DATE('now'), ?, ?, ?)
               ON CONFLICT(category_id, date) DO UPDATE SET
                   deals_found = deals_found + excluded.deals_found,
                   deals_sent = deals_sent + excluded.deals_sent,
                   scrape_errors = scrape_errors + excluded.scrape_errors""",
            (category_id, deals_found, deals_sent, errors),
        )
        await self.conn.commit()
