"""
Telegram bot command handlers.

Commands:
  /start              - Register user
  /setprice <min> <max> - Set price range
  /setlocation <kw1, kw2> - Set location keywords
  /mypreferences      - Show current preferences
  /clearpreferences   - Reset preferences
  /listings           - Trigger manual scrape
  /help               - Help text
"""

import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

from datetime import date as _date
from database import db
from parser.listing_parser import parse_listings, parse_pdf_properties, property_from_db, Property
from bot.notifications import format_listing_message, format_property_message
from scheduler.job_scheduler import _matches_property
from config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pref_summary(pref: dict | None) -> str:
    if not pref:
        return "No preferences set."
    min_p = f"R {pref['min_price']:,.0f}" if pref.get("min_price") is not None else "any"
    max_p = f"R {pref['max_price']:,.0f}" if pref.get("max_price") is not None else "any"
    kws = ", ".join(pref.get("location_keywords") or []) or "any"
    return (
        f"💰 Price range: {min_p} – {max_p}\n"
        f"📍 Location keywords: {kws}"
    )


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(
        telegram_id=user.id,
        chat_id=update.effective_chat.id,
        username=user.username,
    )
    await update.message.reply_text(
        f"👋 Welcome, {user.first_name}!\n\n"
        "I monitor *Sale in Execution* property listings on sheroot.co.za "
        "and notify you when a listing matches your preferences.\n\n"
        "Quick start:\n"
        "  /setprice 500000 1500000\n"
        "  /setlocation Roodepoort, Krugersdorp\n"
        "  /mypreferences – view current settings\n"
        "  /listings – manually check now\n"
        "  /help – full command list",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Available commands:*\n"
        "/start – Register and show welcome\n"
        "/setprice <min> <max> – Set price range (ZAR, no R symbol)\n"
        "/setlocation <kw1, kw2, ...> – Set location keywords\n"
        "/mypreferences – Show your current preferences\n"
        "/clearpreferences – Reset all preferences\n"
        "/listings – Manually trigger a scrape and show results\n"
        "/help – Show this message\n\n"
        "⚡ *No Court Reserve* and 🏦 *Bank Reserve* properties are always "
        "sent regardless of your price filter — these are high-opportunity listings.",
        parse_mode="Markdown",
    )


async def cmd_setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 2:
        await update.message.reply_text(
            "Usage: /setprice <min> <max>\nExample: /setprice 500000 1500000"
        )
        return
    try:
        min_price = float(args[0].replace(",", ""))
        max_price = float(args[1].replace(",", ""))
    except ValueError:
        await update.message.reply_text("Please provide numeric values without the R symbol.")
        return
    if min_price > max_price:
        await update.message.reply_text("Minimum price cannot be greater than maximum price.")
        return

    db.upsert_user(
        telegram_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        username=update.effective_user.username,
    )
    db.upsert_preference(
        telegram_id=update.effective_user.id,
        min_price=min_price,
        max_price=max_price,
    )
    await update.message.reply_text(
        f"✅ Price range set: R {min_price:,.0f} – R {max_price:,.0f}\n\n"
        "⚡ Note: No Court Reserve & Bank Reserve properties are always sent regardless of price range."
    )


async def cmd_setlocation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /setlocation <keyword1, keyword2, ...>\n"
            "Example: /setlocation Roodepoort, Krugersdorp"
        )
        return
    raw = " ".join(context.args)
    keywords = [kw.strip() for kw in raw.split(",") if kw.strip()]
    if not keywords:
        await update.message.reply_text("No valid keywords provided.")
        return

    db.upsert_user(
        telegram_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        username=update.effective_user.username,
    )
    db.upsert_preference(
        telegram_id=update.effective_user.id,
        location_keywords=keywords,
    )
    await update.message.reply_text(
        f"✅ Location keywords set: {', '.join(keywords)}"
    )


async def cmd_mypreferences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pref = db.get_preference(update.effective_user.id)
    await update.message.reply_text(_pref_summary(pref))


async def cmd_clearpreferences(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.clear_preference(update.effective_user.id)
    await update.message.reply_text("✅ Preferences cleared. You will no longer receive notifications.")


async def cmd_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show upcoming property listings, served from DB cache when available."""
    today = _date.today().isoformat()
    pref = db.get_preference(update.effective_user.id)

    # --- Fast path: serve from DB cache ---
    cached_rows = db.get_upcoming_properties(today)
    if cached_rows:
        all_properties = [property_from_db(r) for r in cached_rows]

        if pref:
            filtered = [p for p in all_properties if _matches_property(p, pref)]
            filter_note = " matching your preferences"
        else:
            filtered = all_properties
            filter_note = ""

        cap = 15
        total = len(filtered)
        if total == 0:
            await update.message.reply_text(
                "No properties match your current preferences.\n"
                "Use /clearpreferences to see all listings."
            )
            return

        await update.message.reply_text(
            f"\U0001f4cb {total} propert{'y' if total == 1 else 'ies'}{filter_note} "
            f"(showing first {min(cap, total)}, served from cache):"
        )
        for prop in filtered[:cap]:
            msg = format_property_message(prop)
            await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
        return

    # --- Slow path: no cache yet, do a live scrape ---
    await update.message.reply_text("\U0001f50d No cached data found — scraping now, please wait...")
    try:
        from scraper.sheroot_scraper import scrape_listings
        raw_events = await scrape_listings()
    except Exception as exc:
        logger.exception("Manual scrape failed")
        await update.message.reply_text(f"\u274c Scrape failed: {exc}")
        return

    if not raw_events:
        await update.message.reply_text("No upcoming sale events found at this time.")
        return

    all_properties: list[Property] = []
    for event in raw_events:
        pdf_text = event.get("pdf_text", "")
        pdf_url = event.get("pdf_url", "")
        sale_date = event.get("date", "")
        props = parse_pdf_properties(pdf_text, sale_date, pdf_url)
        # Cache them for next time
        for prop in props:
            db.upsert_property(prop)
        all_properties.extend(props)

    if not all_properties:
        listings = parse_listings(raw_events)
        if not listings:
            await update.message.reply_text("No listings found at this time.")
            return
        await update.message.reply_text(f"Found {len(listings)} listing(s) (summary view):")
        for listing in listings[:10]:
            msg = format_listing_message(listing)
            await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)
        return

    if pref:
        filtered = [p for p in all_properties if _matches_property(p, pref)]
        filter_note = " matching your preferences"
    else:
        filtered = all_properties
        filter_note = ""

    cap = 15
    total = len(filtered)
    await update.message.reply_text(
        f"Found {total} propert{'y' if total == 1 else 'ies'}{filter_note} "
        f"(showing first {min(cap, total)}):"
    )
    for prop in filtered[:cap]:
        msg = format_property_message(prop)
        await update.message.reply_text(msg, parse_mode="Markdown", disable_web_page_preview=True)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def build_application() -> Application:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setprice", cmd_setprice))
    app.add_handler(CommandHandler("setlocation", cmd_setlocation))
    app.add_handler(CommandHandler("mypreferences", cmd_mypreferences))
    app.add_handler(CommandHandler("clearpreferences", cmd_clearpreferences))
    app.add_handler(CommandHandler("listings", cmd_listings))

    return app
