# Property Agent Bot — CLAUDE.md

## Project Overview

A Telegram bot that monitors [sheroot.co.za](https://www.sheroot.co.za/fixed-property-sales.html) for Sale in Execution property listings and notifies users when a listing matches their preferences.

The bot parses **individual property entries** from PDFs (not just calendar events), caches them in a database, and sends per-property notifications with rich formatting.

---

## Running the Bot

```bash
python main.py
```

Requires `.env` in the project root (see `.env` section below). On first run it installs the DB schema, starts the scheduler, and runs an immediate scrape.

---

## Environment Variables (`.env`)

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token | required |
| `TELEGRAM_CHAT_IDS` | Comma-separated chat IDs | optional |
| `CHECK_INTERVAL_HOURS` | Legacy — no longer used (schedule is cron-based) | `6` |
| `DATABASE_PATH` | SQLite file path | `property_agent.db` |

---

## Project Structure

```
property_agent/
├── main.py                     # Entry point — wires bot + scheduler
├── config.py                   # Loads .env, exposes constants
├── scraper/
│   └── sheroot_scraper.py      # Playwright scraper (intercepts Inffuse API)
├── parser/
│   └── listing_parser.py       # Parses PDFs → Property dataclass (per-property)
├── bot/
│   ├── telegram_bot.py         # Command handlers + Application factory
│   └── notifications.py        # Format + send Telegram messages
├── scheduler/
│   └── job_scheduler.py        # APScheduler job, matching logic
├── database/
│   └── db.py                   # SQLite CRUD (users, preferences, properties, seen_listings)
└── inspect_iframe.py           # Dev utility for iframe inspection
```

---

## Scraper Architecture

The Sheroot page embeds an **Inffuse Events Calendar** widget in an iframe. The widget fires an API call to:

```
inffuse.eventscalendar.co/js/v0.1/calendar/data
```

**Do not** try to scrape the iframe DOM — it is a React app that renders asynchronously and selectors will not match.

**Correct approach** (in `sheroot_scraper.py`):
1. Launch Playwright (headless Chromium).
2. Register a `response` listener on the page.
3. Navigate to the Sheroot page and wait for `networkidle`.
4. The listener captures the JSON from the Inffuse API call.
5. Events live at `response["project"]["data"]["events"]`.
6. For each event, follow `links[].url` to fetch the property list page text.

Each event has: `startDate`, `title`, `location`, `startHour`, `startMinutes`, `endHour`, `endMinutes`, `links`, `id`.

**Past events are filtered out** — events with a `startDate` before today are skipped entirely.

### PDF Download & Caching

The linked list pages (`sheroot.co.za/Listfixed1`, `/Listfixed2`) are published close to the sale date and may return 404 beforehand — this is expected and handled gracefully.

When a list page is live, the scraper:
1. Loads the HTML page
2. Finds the `<a href="...pdf">` PDF download link (labelled "Download File")
3. Downloads the PDF via the browser session (carries cookies/session)
4. Extracts all text using `pdfplumber`

**DB Cache Optimization**: The scraper accepts a `skip_dates` parameter — if a sale date is already cached in the `properties` table, PDF download is skipped entirely. This reduces scrape time from ~45s to ~8s on subsequent runs.

If no PDF link is found, it falls back to `inner_text("body")`.

### PDF naming convention

PDFs are hosted at:
```
https://www.sheroot.co.za/uploads/1/2/4/1/124139032/list.{DD}_{month}_{YYYY}.pdf
```
Examples:
- `list.13_february_2026.pdf` — 13 pages, 19 properties
- `list.27_february_2026.pdf` — 14 pages, 22 properties

### PDF structure & parsing

- **Page 1** — Sale date, time, venue, registration instructions
- **Page 2** — Conditions of sale, then property listings begin
- **Pages 3–12** — Individual property entries (address, description, size in m², reserve price)
- **Pages 13–14** — Legal rules of sale in execution

**Per-property parsing**: The `parse_pdf_properties()` function splits the PDF text on numbered headings (`1. `, `2. `, etc.) and extracts:
- Property number
- Full raw text block (address + description interleaved)
- Size in m² (via regex `(\d[\d\s]*)m²`)
- Reserve type and price (see below)

Reserve prices appear as either:
- **`R{amount} Court Reserve`** — minimum bid set by the court (parsed as `reserve_type='court'`, `reserve_price=<amount>`)
- **`Bank Reserve`** — bank sets the floor, amount not published (`reserve_type='bank'`, `reserve_price=None`)
- **`No Court Reserve`** — goes to highest bidder regardless of price (`reserve_type='none'`, `reserve_price=None`)
- **Unknown** — if no reserve pattern matches (`reserve_type='unknown'`, `reserve_price=None`)

---

## Database Schema (SQLite)

**`users`** — `telegram_id` (PK), `chat_id`, `username`, `created_at`

**`preferences`** — `telegram_id` (FK), `min_price`, `max_price`, `location_keywords` (JSON array), `active`

**`properties`** — `property_hash` (PK, SHA-256), `sale_date`, `property_number`, `raw_text`, `size_m2`, `reserve_price`, `reserve_type`, `pdf_url`, `first_seen_at`
- Stores **every property** parsed from PDFs for historical tracking
- Property hash = `SHA-256(sale_date|property_number|raw_text[:80])`
- Used for deduplication and DB cache serving

**`seen_listings`** — `listing_hash` (PK, SHA-256), `first_seen_at`
- Tracks which properties have been notified to users (prevents re-notification)

---

## Telegram Bot Commands

| Command | Description |
|---|---|
| `/start` | Register user |
| `/setprice <min> <max>` | Set price range in ZAR (no R symbol). Example: `/setprice 500000 1500000` |
| `/setlocation <kw1, kw2>` | Set location keywords (comma-separated). Example: `/setlocation Roodepoort, Krugersdorp` |
| `/mypreferences` | Show current preferences |
| `/clearpreferences` | Reset all preferences (removes filtering) |
| `/listings` | Show upcoming properties (served from DB cache when available, or triggers live scrape if cache is empty) |
| `/help` | Show command list |

---

## Matching Logic

A property matches a user preference when:
- **NCR/Bank Reserve always match** — properties with `reserve_type='none'` or `reserve_type='bank'` bypass all filters (high-opportunity listings)
- **Price check** — if `reserve_price` is not None: must fall within `[min_price, max_price]`
- **Location check** — if `location_keywords` are set: at least one keyword must appear in `raw_text` (case-insensitive)
- **Unknown price** — properties with `reserve_price=None` (but not NCR/Bank) are allowed through

**Notification behavior**:
- If no users have preferences: all new properties are sent to all registered users
- If preferences exist: properties are sent only to users whose preferences match
- Properties are marked "seen" only after at least one notification is sent (prevents spam on re-scrapes)

---

## Scheduler

The scheduler runs a scrape job **every Thursday and Friday at 08:00** (local time) using APScheduler cron triggers.

**Job flow**:
1. Check which sale dates are already cached in DB (`get_sale_dates_in_db`)
2. Pass `skip_dates` to scraper — skips PDF download for cached dates
3. For cached dates: load properties from DB via `property_from_db()`
4. For new dates: parse PDF, save to DB via `upsert_property()`
5. For each unseen property: match against user preferences, send notifications
6. Mark properties as seen after notification

---

## Key Dependencies

- `python-telegram-bot` — Telegram bot framework
- `playwright` — Headless browser scraping
- `pdfplumber` — PDF text extraction
- `apscheduler` — Periodic job scheduling
- `python-dotenv` — `.env` loading

Install Playwright browsers once:
```bash
playwright install chromium
```

---

## Windows Notes

- Always add `sys.stdout.reconfigure(encoding="utf-8")` in `__main__` blocks to avoid `cp1252` errors with the `–` (en-dash) character in event time strings and bot messages.
