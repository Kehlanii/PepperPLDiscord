import logging

logger = logging.getLogger("PepperBot.Pricing")

_FREE_KEYWORDS = ("darm", "free", "bezpłatn")


def parse_price(raw: str | None) -> float | None:
    """Parse Polish price string. Returns None if unparseable, 0.0 for free items."""
    if not raw:
        return None
    try:
        clean = (
            raw.lower()
            .replace("zł", "")
            .replace("pln", "")
            .replace(" ", "")
            .replace(",", ".")
            .strip()
        )
        if any(kw in clean for kw in _FREE_KEYWORDS):
            return 0.0
        return float(clean)
    except ValueError:
        logger.debug("Unparseable price: %s", raw)
        return None
