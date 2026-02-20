"""
Format and send property-match notifications to Telegram users.
"""

import logging
import re
from datetime import datetime
from telegram import Bot
from telegram.error import TelegramError
from parser.listing_parser import Listing, Property

logger = logging.getLogger(__name__)

SOURCE_URL = "https://www.sheroot.co.za/fixed-property-sales.html"

# Lines to strip from raw_text to avoid duplicating size / reserve lines
_CLEANUP_RE = re.compile(
    r'^\s*(?:\d[\d\s]*m\u00b2|No\s+Court\s+Reserve|Bank\s+Reserve|R[\d\s,]+Court\s+Reserve.*)\s*$',
    re.IGNORECASE | re.MULTILINE,
)


def format_listing_message(listing: Listing) -> str:
    price_str = f"R {listing.price:,.0f}" if listing.price else "Price not listed"
    location_str = listing.location or "Unknown"
    erf_str = listing.erf_number or "N/A"
    prop_type_str = listing.property_type.title() if listing.property_type else "N/A"
    date_str = listing.date or "TBD"

    # Truncate description to avoid Telegram's 4096-char limit
    desc = listing.description
    if len(desc) > 1500:
        desc = desc[:1497] + "..."

    msg = (
        f"\U0001f3e0 *NEW SALE IN EXECUTION MATCH*\n"
        f"\U0001f4c5 Date: {date_str}\n"
        f"\U0001f4cd Location: {location_str}\n"
        f"\U0001f4b0 Price: {price_str}\n"
        f"\U0001f3d7 Type: {prop_type_str}\n"
        f"\U0001f4dd ERF: {erf_str}\n"
        f"---\n"
        f"{desc}\n"
        f"---\n"
        f"\U0001f517 [Source: sheroot.co.za]({SOURCE_URL})"
    )
    return msg


def format_property_message(prop: Property) -> str:
    """Format a single Property as a Telegram Markdown message."""
    # Human-readable sale date
    try:
        dt = datetime.strptime(prop.sale_date, "%Y-%m-%d")
        date_str = f"{dt.day} {dt.strftime('%b')} {dt.year}"
    except (ValueError, TypeError):
        date_str = prop.sale_date or "TBD"

    # Clean raw_text: remove size and reserve lines to avoid duplication
    body = _CLEANUP_RE.sub('', prop.raw_text).strip()
    # Collapse multiple blank lines
    body = re.sub(r'\n{3,}', '\n\n', body)
    if len(body) > 600:
        body = body[:597] + "..."

    size_line = f"\U0001f4d0 {prop.size_m2:,.0f}m\u00b2\n" if prop.size_m2 else ""
    link_line = f"\U0001f517 [Full property list]({prop.pdf_url})" if prop.pdf_url else f"\U0001f517 [Sheroot]({SOURCE_URL})"

    msg = (
        f"\U0001f3e0 *Sale in Execution \u2014 {date_str}*\n"
        f"{body}\n"
        f"{size_line}"
        f"{prop.reserve_display}\n"
        f"{link_line}"
    )
    return msg


async def send_notification(bot: Bot, chat_id: int, listing) -> bool:
    """Send a formatted notification. Accepts Listing or Property. Returns True on success."""
    if isinstance(listing, Property):
        message = format_property_message(listing)
        log_label = f"property #{listing.number} on {listing.sale_date}"
    else:
        message = format_listing_message(listing)
        log_label = listing.title
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=False,
        )
        logger.info("Notification sent to chat_id=%s for %s", chat_id, log_label)
        return True
    except TelegramError as exc:
        logger.error("Failed to send notification to chat_id=%s: %s", chat_id, exc)
        return False
