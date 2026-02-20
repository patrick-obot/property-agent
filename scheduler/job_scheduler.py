"""
APScheduler-based periodic scrape job.

The job:
1. Scrapes Sheroot for new calendar events.
2. Parses each event's PDF into individual Property objects.
3. Saves every property to the DB for historical tracking.
4. For each unseen property, matches against active user preferences.
5. Sends per-property notifications and marks properties as seen.
"""

import logging
from datetime import date as _date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

from config import CHECK_INTERVAL_HOURS
from database import db
from scraper.sheroot_scraper import scrape_listings
from parser.listing_parser import parse_pdf_properties, property_from_db, Property
from bot.notifications import send_notification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Matching logic
# ---------------------------------------------------------------------------

def _matches_property(prop: Property, pref: dict) -> bool:
    """
    Return True if the property satisfies the user's preference.
    NCR and Bank Reserve properties always match (bypass price filter).
    """
    # Opportunities always go through
    if prop.is_opportunity:
        return True

    # Price check
    if prop.reserve_price is not None:
        min_p = pref.get("min_price")
        max_p = pref.get("max_price")
        if min_p is not None and prop.reserve_price < min_p:
            return False
        if max_p is not None and prop.reserve_price > max_p:
            return False
    # If no price found, allow through

    # Location keyword check
    keywords: list[str] = pref.get("location_keywords") or []
    if keywords:
        text_lower = prop.raw_text.lower()
        matched = any(kw.lower() in text_lower for kw in keywords)
        if not matched:
            return False

    return True


# ---------------------------------------------------------------------------
# Core scrape-and-notify job
# ---------------------------------------------------------------------------

async def scrape_and_notify(bot: Bot):
    """Scrape listings, match against preferences, send notifications."""
    logger.info("Starting scheduled scrape job...")

    today = _date.today().isoformat()

    # Dates already in DB — we can skip re-downloading their PDFs
    cached_dates = db.get_sale_dates_in_db(today)
    if cached_dates:
        logger.info("DB cache covers sale dates: %s — PDF download will be skipped", cached_dates)

    try:
        raw_events = await scrape_listings(skip_dates=cached_dates)
    except Exception:
        logger.exception("Scraping failed during scheduled job.")
        return

    logger.info("Raw events returned: %d", len(raw_events))

    users = {u["telegram_id"]: dict(u) for u in db.get_all_users()}
    preferences = db.get_all_active_preferences()

    total_new = 0

    for event in raw_events:
        sale_date = event.get("date", "")

        if sale_date in cached_dates:
            # Load properties from DB cache instead of re-parsing
            cached_rows = [
                r for r in db.get_upcoming_properties(today)
                if r["sale_date"] == sale_date
            ]
            properties = [property_from_db(r) for r in cached_rows]
            logger.info("Event '%s' (%s): using %d cached properties from DB",
                        event.get("title"), sale_date, len(properties))
        else:
            pdf_text = event.get("pdf_text", "")
            pdf_url = event.get("pdf_url", "")
            properties = parse_pdf_properties(pdf_text, sale_date, pdf_url)
            logger.info("Event '%s' (%s): parsed %d properties from PDF",
                        event.get("title"), sale_date, len(properties))
            for prop in properties:
                db.upsert_property(prop)

        for prop in properties:
            h = prop.property_hash()
            if db.is_listing_seen(h):
                continue

            notified: set[int] = set()

            if not preferences:
                # No prefs: send to all registered users
                for tid, user in users.items():
                    await send_notification(bot, user["chat_id"], prop)
                    notified.add(tid)
            else:
                for pref in preferences:
                    if _matches_property(prop, pref):
                        user = users.get(pref["telegram_id"])
                        if user and pref["telegram_id"] not in notified:
                            await send_notification(bot, user["chat_id"], prop)
                            notified.add(pref["telegram_id"])

            if notified:
                db.mark_listing_seen(h)
                total_new += 1

    logger.info("Scrape job done. New properties notified: %d", total_new)


# ---------------------------------------------------------------------------
# Scheduler factory
# ---------------------------------------------------------------------------

def build_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    # Run every Thursday (3) and Friday (4) at 08:00 local time
    scheduler.add_job(
        scrape_and_notify,
        trigger="cron",
        day_of_week="thu,fri",
        hour=8,
        minute=0,
        args=[bot],
        id="scrape_job",
        replace_existing=True,
    )
    return scheduler
