BEGIN TRANSACTION;

DROP INDEX IF EXISTS idx_category_stats_date;
DROP INDEX IF EXISTS idx_category_sent_time;
DROP INDEX IF EXISTS idx_category_sent_lookup;
DROP INDEX IF EXISTS idx_category_slug;
DROP INDEX IF EXISTS idx_category_guild_active;
DROP INDEX IF EXISTS idx_category_status;
DROP INDEX IF EXISTS idx_category_guild_id;

DROP TABLE IF EXISTS category_stats;
DROP TABLE IF EXISTS category_sent_deals;
DROP TABLE IF EXISTS category_configs;

SELECT COUNT(*) as sent_deals_check FROM sent_deals;
SELECT COUNT(*) as alerts_check FROM alerts;

COMMIT;