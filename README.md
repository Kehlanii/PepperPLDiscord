# PepperPL Discord Bot

Discord bot that scrapes [Pepper.pl](https://www.pepper.pl) for deals, flight alerts, and category notifications. Built with `discord.py`, uses `selectolax` for fast HTML parsing.

## Requirements

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (package manager)
- Discord Bot Token

## Setup

```bash
git clone https://github.com/Kehlanii/PepperPLDiscord.git
cd PepperPLDiscord
cp .env.example .env  # edit with your token
uv sync
uv run bot.py
```

### `.env` file

```env
DISCORD_BOT_TOKEN=your_token_here
FLIGHT_CHANNEL_ID=1448267942826475574
FLIGHT_SCHEDULE_HOUR=8
```

## Commands

### Slash Commands

| Command | What it does |
|---------|-------------|
| `/pepper <query>` | Search Pepper.pl for deals |
| `/pepperhot` | Get hottest deals from front page |
| `/pepper_group <slug>` | Get deals from a category (e.g. `elektronika`, `gry`) |
| `/pepperclean [limit]` | Delete bot's last N messages (default 20) |
| `/flynow` | Manually trigger daily flight report |

#### PepperWatch (alert system)

| Command | What it does |
|---------|-------------|
| `/pepperwatch add <query> [max_price]` | Watch a query, get DM when new deals match |
| `/pepperwatch list` | Show your active alerts |
| `/pepperwatch remove <query>` | Stop watching a query |

#### Category Automation

| Command | What it does |
|---------|-------------|
| `/category add <slug> <freq> <time> <channel> [day] [date] [min_temp] [max_price]` | Add scheduled category notifications (admin only) |
| `/category list` | Show all active categories |
| `/category remove <slug>` | Remove a category (admin only) |
| `/category trigger <slug>` | Manually trigger a category check (admin only) |
| `/category pause <slug>` | Pause a category (admin only) |
| `/category resume <slug>` | Resume a paused category (admin only) |
| `/category preview <slug>` | Preview deals before adding a category |

### Text Commands (prefix: `p `)

All text commands use the `p ` prefix (with a space).

| Command | What it does |
|---------|-------------|
| `p <query>` | Search for deals (anything not matching other commands) |
| `p hot` | Get hottest deals |
| `p group:<slug>` | Get deals from a category |
| `p preview:<slug>` | Preview a category's deals |
| `p watch:<query>` | Watch a query for new deals |
| `p watch:<query> < 500` | Watch with max price filter |
| `p unwatch:<query>` | Stop watching |
| `p alerts` or `p list` | Show your alerts |
| `p fly` | Trigger flight report (admin only) |
| `p clean [N]` | Delete bot's last N messages |
| `p cat list` | List categories |
| `p cat add:<slug> <freq> <time> <#channel> [day] [min:N] [max:N]` | Add category (admin) |
| `p cat rm:<slug>` | Remove category (admin) |
| `p cat pause:<slug>` | Pause category (admin) |
| `p cat resume:<slug>` | Resume category (admin) |
| `p cat run:<slug>` | Trigger category manually (admin) |

### Category Schedule Examples

```
# Daily at 9:00 to #deals channel
p cat add:elektronika daily 09:00 #deals

# Weekly on Monday at 18:00 with min temperature 100°
p cat add:gry weekly 18:00 #gaming monday min:100

# Monthly on the 1st at 10:00 with max price 500 PLN
p cat add:lego monthly 10:00 #lego 1 max:500

# Same thing with slash command
/category add slug:elektronika frequency:daily time:09:00 channel:#deals
```

### Available Category Slugs

These are Pepper.pl group slugs you can use:

`bilety-lotnicze` · `podzespoly-komputerowe` · `smartfony` · `gry` · `lego` · `laptopy` · `dom-i-ogrod` · `narzedzia` · `elektronika` · `konsole` · `moda-i-akcesoria` · `zabawki` · `sport-i-wypoczynek` · `ksiazki` · `zdrowie-i-uroda` · `jedzenie-i-napoje` · `dom-i-meble` · `tv-audio-foto` · `auto-moto`

Any valid Pepper.pl group slug works — these are just the ones with built-in emoji.

## How It Works

- **Search**: scrapes Pepper.pl, filters by temperature (≥50°), freshness (<24h), and valid price
- **Alerts (PepperWatch)**: checks every 15 minutes, sends DMs with new matches
- **Categories**: scheduled notifications (daily/weekly/biweekly/monthly) to configured channels
- **Flights**: daily flight deal digest at configured hour (default 8:00)

## Project Structure

```
├── bot.py                  # Entry point, PepperBot class
├── cogs/
│   ├── search.py           # /pepper, /pepperhot, /pepper_group
│   ├── alerts.py           # /pepperwatch, alert check task loop
│   ├── categories.py       # /category, scheduled notifications
│   ├── flights.py          # /flynow, daily flight digest
│   └── text_commands.py    # 'p ' prefix router
├── utils/
│   ├── config.py           # Constants and env vars
│   ├── db.py               # SQLite (aiosqlite, WAL mode)
│   ├── scraper.py          # Pepper.pl scraper (selectolax)
│   ├── alerts.py           # AlertsManager (check/add/remove)
│   ├── category_manager.py # Schedule validation, should_run_now
│   ├── deal_filter.py      # Temperature/freshness/price filters
│   ├── pricing.py          # Polish price string parser
│   ├── embeds.py           # Shared embed helpers
│   └── views.py            # Deal paginator (buttons)
├── migrations/             # SQL migrations
├── pyproject.toml
└── pepper.service          # systemd unit file
```

## Running as a Service

```bash
sudo cp pepper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pepper
sudo systemctl start pepper

# Check logs
journalctl -u pepper -f
```

## Dev

```bash
uv sync --extra dev
uv run ruff check .           # lint
uv run mypy .                 # type check
uv run pytest                 # tests
```

---

# PepperPL Discord Bot (PL)

Bot Discord do scrapowania okazji z [Pepper.pl](https://www.pepper.pl). Obsługuje wyszukiwanie, alerty cenowe, automatyczne powiadomienia kategoriowe i raporty lotnicze.

## Wymagania

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (menedżer pakietów)
- Token bota Discord

## Instalacja

```bash
git clone https://github.com/Kehlanii/PepperPLDiscord.git
cd PepperPLDiscord
cp .env.example .env  # wpisz swój token
uv sync
uv run bot.py
```

### Plik `.env`

```env
DISCORD_BOT_TOKEN=twoj_token
FLIGHT_CHANNEL_ID=1448267942826475574
FLIGHT_SCHEDULE_HOUR=8
```

## Komendy

### Komendy Slash

| Komenda | Opis |
|---------|------|
| `/pepper <zapytanie>` | Szukaj okazji na Pepper.pl |
| `/pepperhot` | Najgorętsze okazje ze strony głównej |
| `/pepper_group <slug>` | Okazje z kategorii (np. `elektronika`, `gry`) |
| `/pepperclean [limit]` | Usuń ostatnie wiadomości bota (domyślnie 20) |
| `/flynow` | Ręczne wywołanie raportu lotniczego |

#### PepperWatch (system alertów)

| Komenda | Opis |
|---------|------|
| `/pepperwatch add <fraza> [max_price]` | Obserwuj frazę, dostaniesz DM jak pojawi się okazja |
| `/pepperwatch list` | Pokaż aktywne alerty |
| `/pepperwatch remove <fraza>` | Usuń alert |

#### Automatyzacja Kategorii

| Komenda | Opis |
|---------|------|
| `/category add <slug> <częstotliwość> <czas> <kanał> [dzień] [data] [min_temp] [max_price]` | Dodaj zaplanowane powiadomienia (tylko admin) |
| `/category list` | Pokaż aktywne kategorie |
| `/category remove <slug>` | Usuń kategorię (tylko admin) |
| `/category trigger <slug>` | Ręczne wywołanie sprawdzenia (tylko admin) |
| `/category pause <slug>` | Wstrzymaj kategorię (tylko admin) |
| `/category resume <slug>` | Wznów kategorię (tylko admin) |
| `/category preview <slug>` | Podgląd okazji przed dodaniem kategorii |

### Komendy Tekstowe (prefix: `p `)

| Komenda | Opis |
|---------|------|
| `p <zapytanie>` | Szukaj okazji |
| `p hot` | Najgorętsze okazje |
| `p group:<slug>` | Okazje z kategorii |
| `p preview:<slug>` | Podgląd kategorii |
| `p watch:<fraza>` | Obserwuj frazę |
| `p watch:<fraza> < 500` | Obserwuj z limitem ceny |
| `p unwatch:<fraza>` | Usuń obserwację |
| `p alerts` lub `p list` | Pokaż alerty |
| `p fly` | Raport lotniczy (admin) |
| `p clean [N]` | Usuń wiadomości bota |
| `p cat list` | Lista kategorii |
| `p cat add:<slug> <freq> <czas> <#kanał> [dzień] [min:N] [max:N]` | Dodaj kategorię (admin) |
| `p cat rm:<slug>` | Usuń kategorię (admin) |
| `p cat pause:<slug>` | Wstrzymaj (admin) |
| `p cat resume:<slug>` | Wznów (admin) |
| `p cat run:<slug>` | Uruchom ręcznie (admin) |

### Przykłady Konfiguracji Kategorii

```
# Codziennie o 9:00 na kanał #deals
p cat add:elektronika daily 09:00 #deals

# Co tydzień w poniedziałek o 18:00, min temperatura 100°
p cat add:gry weekly 18:00 #gaming monday min:100

# Co miesiąc 1. dnia o 10:00, max cena 500 zł
p cat add:lego monthly 10:00 #lego 1 max:500
```

## Jak to działa

- **Wyszukiwanie**: scrapuje Pepper.pl, filtruje po temperaturze (≥50°), świeżości (<24h) i cenie
- **Alerty (PepperWatch)**: sprawdza co 15 minut, wysyła DM z nowymi okazjami
- **Kategorie**: zaplanowane powiadomienia (dziennie/tygodniowo/dwutygodniowo/miesięcznie)
- **Loty**: codzienny raport o lotach o skonfigurowanej godzinie (domyślnie 8:00)

## Uruchomienie jako serwis

```bash
sudo cp pepper.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable pepper
sudo systemctl start pepper

# Logi
journalctl -u pepper -f
```
