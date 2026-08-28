import os


class Config:
    # Embed colors
    COLOR_PRIMARY = 0xFF6B35
    COLOR_SUCCESS = 0x00FF00
    COLOR_ERROR = 0xFF0000
    COLOR_WARNING = 0xFFA500
    COLOR_NEUTRAL = 0x808080

    DEFAULT_SEARCH_LIMIT = 7
    MAX_CLEAN_LIMIT = 100

    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    BASE_URL = "https://www.pepper.pl"
    FLIGHT_CATEGORY_URL = f"{BASE_URL}/grupa/bilety-lotnicze"
    GROUP_URL_TEMPLATE = f"{BASE_URL}/grupa/{{}}"

    # From .env with hardcoded fallback for backwards compat
    FLIGHT_CHANNEL_ID = int(os.getenv("FLIGHT_CHANNEL_ID", "1448267942826475574"))
    FLIGHT_SCHEDULE_HOUR = int(os.getenv("FLIGHT_SCHEDULE_HOUR", "8"))

    WATCH_INTERVAL_MINUTES = 15

    # Category scheduling
    MAX_CATEGORIES_PER_GUILD = 20
    CATEGORY_STAGGER_DELAY = 2
    MAX_DEALS_PER_NOTIFICATION = 10
    CLEANUP_INTERVAL_HOURS = 24
    CLEANUP_DAYS_OLD = 30
