# Pepper.pl Discord Bot

A high-performance Discord bot that scrapes [Pepper.pl](https://www.pepper.pl) for deals, provides automated notifications, and allows users to set up custom price alerts. Built for speed and reliability using asynchronous Python.

## Overview

This bot serves as a bridge between the Pepper.pl deal platform and Discord. It bypasses the need for RSS feeds by directly scraping the website using optimized HTML parsing. It supports on-demand searching, personal keyword alerts (sent via DM), and automated channel feeds for specific categories (e.g., "GPU < 3000 PLN").

## Features

*   **Deal Searching**: Query Pepper.pl directly from Discord (`/pepper [query]`, `/pepperhot`).
*   **Personal Alerts (PepperWatch)**: Users can subscribe to keywords (e.g., "RTX 4070") with optional price caps. The bot checks every 15 minutes and DMs matches.
*   **Automated Categories**: Server admins can set up dedicated channels for specific deal categories (e.g., "gaming", "home-automation") with customizable schedules and filters.
*   **Flight Deal Digest**: Daily automated report of flight deals.
*   **High-Performance Scraping**: Uses `selectolax` (C-based HTML parser) and Vue.js hydration data for fast, reliable extraction.
*   **Duplicate Protection**: Tracks sent deals in SQLite to prevent spamming the same offer twice.

## Tech Stack

*   **Language**: Python 3.10+
*   **Framework**: [discord.py](https://github.com/Rapptz/discord.py) (Asynchronous interaction-based bot)
*   **Scraping**: [selectolax](https://github.com/rushter/selectolax) (Ultra-fast HTML parsing), `aiohttp` (Async HTTP requests)
*   **Database**: `aiosqlite` (Async SQLite3 for lightweight, file-based persistence)
*   **Configuration**: `python-dotenv`

## Setup

### Prerequisites
*   Python 3.10 or higher
*   A Discord Bot Token (from [Discord Developer Portal](https://discord.com/developers/applications))
*   [uv](https://github.com/astral-sh/uv) (Recommended for 10-100x faster dependency resolution)

### Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/Kehlanii/DiscordPepperPL.git
    cd DiscordPepperPL
    ```

2.  **Fast Setup (Recommended with `uv`):**
    ```bash
    # Create venv and install dependencies in one go (extremely fast)
    uv venv
    source .venv/bin/activate  # or .venv\Scripts\activate on Windows
    uv pip install -r requirements.txt
    ```

    *Alternative (Traditional pip):*
    ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    ```

3.  **Configuration:**
    Create a `.env` file in the root directory:
    ```bash
    touch .env
    ```
    Add your bot token:
    ```env
    DISCORD_BOT_TOKEN=your_token_here
    ```

4.  **Run the bot:**
    ```bash
    python bot.py
    ```

## Commands

| Command | Description | Permissions |
| :--- | :--- | :--- |
| `/pepper [query]` | Search for deals matching a keyword. | Everyone |
| `/pepperhot` | Fetch the current hottest deals from the homepage. | Everyone |
| `/pepper_group [slug]` | Get deals from a specific category (e.g., `gry`, `elektronika`). | Everyone |
| `/pepperwatch add [query] [max_price]` | Add a personal alert. DMs you when found. | Everyone |
| `/pepperwatch list` | View your active personal alerts. | Everyone |
| `/pepperwatch remove [query]` | Remove a personal alert. | Everyone |
| `/category add [slug] ...` | Setup an automated feed for a category into a channel. | Admin |
| `/category list` | List all active category feeds on the server. | Everyone |
| `/category pause/resume` | Temporarily stop/start a category feed. | Admin |
| `/flynow` | Manually trigger the flight deal report. | Admin |

## Architecture

The bot is structured around a central asynchronous loop with several key components:

### 1. The Scraper (`utils/scraper.py`)
Instead of using heavy browser automation (Selenium/Playwright) or slow parsers (BeautifulSoup), this bot uses `selectolax`.
*   **Strategy**: It attempts to locate the Vue.js hydration data (`data-vue3` attribute) injected into the HTML. This contains raw JSON data, which is faster and more reliable to parse than DOM elements.
*   **Fallback**: If Vue data is missing, it falls back to CSS selectors to scrape the rendered HTML.

### 2. Database (`utils/db.py`)
Uses SQLite for persistence. Key tables:
*   `sent_deals`: Tracks IDs of deals already posted to prevent duplicates.
*   `alerts`: Stores user-defined keywords and price limits.
*   `alert_history`: Prevents a user from being alerted twice for the same deal.
*   `category_configs`: Stores settings for server-wide automated feeds.

### 3. Task Loops (`cogs/pepper.py`)
The bot runs background tasks using `discord.ext.tasks`:
*   **Alerts Task**: Runs every 15 mins. Scrapes search results for every user query and DMs matches.
*   **Category Task**: Runs every minute. Checks if any configured category needs to be scraped based on its schedule (Daily/Weekly/etc).
*   **Flight Task**: Runs daily at 08:00 AM (configurable).

## Configuration

Core settings are defined in `utils/config.py`.

*   `FLIGHT_SCHEDULE_HOUR`: Hour to send flight reports (Default: 8).
*   `WATCH_INTERVAL_MINUTES`: Frequency of checking user alerts (Default: 15).
*   `DEFAULT_SEARCH_LIMIT`: Number of results to show in search (Default: 7).

## Deployment

For production, it is recommended to run the bot as a systemd service.

1.  Create service file: `/etc/systemd/system/pepperbot.service`
    ```ini
    [Unit]
    Description=Pepper.pl Discord Bot
    After=network.target

    [Service]
    Type=simple
    User=your_user
    WorkingDirectory=/path/to/DiscordPepperPL
    ExecStart=/path/to/DiscordPepperPL/venv/bin/python bot.py
    Restart=always
    RestartSec=10

    [Install]
    WantedBy=multi-user.target
    ```

2.  Enable and start:
    ```bash
    sudo systemctl enable pepperbot
    sudo systemctl start pepperbot
    ```

## Known Issues

*   **Rate Limiting**: Aggressive scraping may trigger Cloudflare protection or IP bans from Pepper.pl. The scraper includes basic delays and user-agent rotation logic, but use reasonable intervals.
*   **Hardcoded Flight Channel**: The flight deal channel ID is currently hardcoded in `Config.FLIGHT_CHANNEL_ID`. This needs to be changed in code before deployment.
