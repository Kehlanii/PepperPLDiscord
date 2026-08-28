BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS category_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    slug TEXT NOT NULL,
    name TEXT,
    channel_id INTEGER NOT NULL,
    schedule_type TEXT NOT NULL CHECK(schedule_type IN ('daily', 'weekly', 'biweekly', 'monthly')),
    schedule_time TEXT NOT NULL,
    schedule_day TEXT,
    schedule_date INTEGER,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'paused', 'disabled')),
    min_temperature INTEGER DEFAULT 0,
    max_price REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_run TIMESTAMP,
    UNIQUE(guild_id, slug)
);

CREATE TABLE IF NOT EXISTS category_sent_deals (
    category_id INTEGER NOT NULL,
    deal_id TEXT NOT NULL,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(category_id) REFERENCES category_configs(id) ON DELETE CASCADE,
    PRIMARY KEY(category_id, deal_id)
);

CREATE TABLE IF NOT EXISTS category_stats (
    category_id INTEGER NOT NULL,
    date DATE NOT NULL,
    deals_found INTEGER DEFAULT 0,
    deals_sent INTEGER DEFAULT 0,
    scrape_errors INTEGER DEFAULT 0,
    FOREIGN KEY(category_id) REFERENCES category_configs(id) ON DELETE CASCADE,
    PRIMARY KEY(category_id, date)
);

CREATE INDEX IF NOT EXISTS idx_category_guild_id ON category_configs(guild_id);
CREATE INDEX IF NOT EXISTS idx_category_status ON category_configs(status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_category_guild_active ON category_configs(guild_id, status);
CREATE INDEX IF NOT EXISTS idx_category_slug ON category_configs(guild_id, slug);

CREATE INDEX IF NOT EXISTS idx_category_schedule 
ON category_configs(status, schedule_type, schedule_time) 
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_category_sent_lookup ON category_sent_deals(category_id, deal_id);
CREATE INDEX IF NOT EXISTS idx_category_sent_time ON category_sent_deals(sent_at);


CREATE INDEX IF NOT EXISTS idx_category_stats_date ON category_stats(category_id, date);

SELECT COUNT(*) as sent_deals_intact FROM sent_deals;
SELECT COUNT(*) as alerts_intact FROM alerts;
SELECT COUNT(*) as alert_history_intact FROM alert_history;

COMMIT;