"""
Scraper for the Sheroot fixed property sales page.

Strategy: Intercept the Inffuse calendar API response
  (inffuse.eventscalendar.co/js/v0.1/calendar/data)
that is fired by the embed.js widget on the Sheroot page.
The response contains structured JSON event data at
  response["project"]["data"]["events"]

Each event may also link to a detail/list page (e.g. sheroot.co.za/Listfixed1)
whose text is fetched and appended to the raw_text for downstream parsing.
"""

import asyncio
import io
import logging
import re
from datetime import datetime, timezone

import pdfplumber
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

SHEROOT_URL = "https://www.sheroot.co.za/fixed-property-sales.html"
CALENDAR_API_PATH = "inffuse.eventscalendar.co/js/v0.1/calendar/data"
SHEROOT_BASE = "https://www.sheroot.co.za"

_PDF_LINK_RE = re.compile(r'href=["\']([^"\']+\.pdf)["\']', re.IGNORECASE)


def _ms_to_iso(ms: int) -> str:
    """Convert millisecond epoch to ISO date string (YYYY-MM-DD)."""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF given its raw bytes."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = []
            for i, p in enumerate(pdf.pages, 1):
                text = p.extract_text() or ""
                if text.strip():
                    pages_text.append(f"[Page {i}]\n{text}")
            result = "\n\n".join(pages_text)
            logger.info("PDF extracted: %d page(s), %d chars", len(pdf.pages), len(result))
            return result
    except Exception as exc:
        logger.warning("PDF extraction failed: %s", exc)
        return ""


async def _fetch_list_page(page, url: str) -> tuple[str, str]:
    """
    Navigate to a listing detail URL.
    If the page contains a PDF download link, download and extract the PDF text.
    Otherwise return the page body text.
    Returns (text, pdf_url) — pdf_url is "" if no PDF was found.
    """
    if not url.startswith("http"):
        if url.startswith("www."):
            url = "https://" + url
        else:
            url = SHEROOT_BASE + "/" + url.lstrip("/")
    try:
        new_page = await page.context.new_page()
        await new_page.goto(url, timeout=30_000, wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Check for a PDF download link in the page HTML
        html = await new_page.content()
        pdf_match = _PDF_LINK_RE.search(html)

        if pdf_match:
            pdf_href = pdf_match.group(1)
            if not pdf_href.startswith("http"):
                pdf_href = SHEROOT_BASE + "/" + pdf_href.lstrip("/")
            logger.info("Found PDF link on list page: %s", pdf_href)

            # Download the PDF via the existing browser context (carries cookies/session)
            response = await new_page.context.request.get(pdf_href)
            if response.ok:
                pdf_bytes = await response.body()
                logger.info("Downloaded PDF: %d bytes", len(pdf_bytes))
                await new_page.close()
                return _extract_pdf_text(pdf_bytes), pdf_href
            else:
                logger.warning("PDF download failed: HTTP %s for %s", response.status, pdf_href)

        # Fallback: return plain body text
        text = await new_page.inner_text("body")
        await new_page.close()
        return text.strip(), ""

    except Exception as exc:
        logger.warning("Could not fetch list page %s: %s", url, exc)
        return "", ""


async def scrape_listings(skip_dates: set | None = None) -> list[dict]:
    """
    Returns a list of raw event dicts scraped from Sheroot.
    Each dict has keys: title, date, description, raw_text, links, event_id,
    pdf_text, pdf_url.

    skip_dates: sale dates already cached in DB — PDF download is skipped for
    these dates (pdf_text and pdf_url will be empty strings in the returned dict).
    """
    all_events: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        calendar_data: dict | None = None

        async def on_response(resp):
            nonlocal calendar_data
            if CALENDAR_API_PATH in resp.url:
                try:
                    calendar_data = await resp.json()
                    logger.info("Intercepted calendar data API: %s", resp.url)
                except Exception as exc:
                    logger.error("Failed to parse calendar JSON: %s", exc)

        page.on("response", on_response)

        logger.info("Loading Sheroot page: %s", SHEROOT_URL)
        try:
            await page.goto(SHEROOT_URL, timeout=60_000, wait_until="networkidle")
        except PlaywrightTimeout:
            logger.error("Timed out loading %s", SHEROOT_URL)
            await browser.close()
            return all_events

        # Give embed.js time to fire the API call
        await asyncio.sleep(5)

        if not calendar_data:
            logger.warning("Calendar API response not captured. No events returned.")
            await browser.close()
            return all_events

        raw_events = (
            calendar_data.get("project", {})
            .get("data", {})
            .get("events", [])
        )
        logger.info("Events found in API response: %d", len(raw_events))

        today = datetime.now(tz=timezone.utc).date()

        for ev in raw_events:
            title = ev.get("title", "")
            start_date = ev.get("startDate") or _ms_to_iso(ev.get("start", 0))

            # Skip events that have already passed
            try:
                event_date = datetime.strptime(start_date, "%Y-%m-%d").date()
                if event_date < today:
                    logger.info("Skipping past event '%s' on %s", title, start_date)
                    continue
            except (ValueError, TypeError):
                pass  # If date can't be parsed, include the event anyway
            location = ev.get("location", "")
            event_id = ev.get("id", "")
            links = ev.get("links", [])

            # Build base description from calendar fields
            start_hour = ev.get("startHour", 0)
            start_min = ev.get("startMinutes", 0)
            end_hour = ev.get("endHour", 0)
            end_min = ev.get("endMinutes", 0)
            time_str = f"{start_hour:02d}:{start_min:02d} – {end_hour:02d}:{end_min:02d}"

            description_parts = [
                f"Title: {title}",
                f"Date: {start_date}",
                f"Time: {time_str}",
                f"Location: {location}",
            ]

            # Fetch linked list pages for property detail text
            # Skip if this date is already cached in the DB
            pdf_text = ""
            pdf_url = ""
            if skip_dates and start_date in skip_dates:
                logger.info("Skipping PDF fetch for cached date %s", start_date)
            else:
                for link in links:
                    link_url = link.get("url", "")
                    if link_url:
                        logger.info("Fetching linked list page: %s", link_url)
                        list_text, found_pdf_url = await _fetch_list_page(page, link_url)
                        if list_text:
                            description_parts.append(f"\n--- Property List ---\n{list_text}")
                            pdf_text = list_text
                        if found_pdf_url:
                            pdf_url = found_pdf_url

            raw_text = "\n".join(description_parts)

            all_events.append(
                {
                    "title": title,
                    "date": start_date,
                    "description": raw_text,
                    "raw_text": raw_text,
                    "links": links,
                    "event_id": event_id,
                    "location": location,
                    "pdf_text": pdf_text,
                    "pdf_url": pdf_url,
                }
            )

        await browser.close()

    logger.info("Total events scraped: %d", len(all_events))
    return all_events


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    results = asyncio.run(scrape_listings())
    for i, evt in enumerate(results, 1):
        print(f"\n{'='*60}")
        print(f"Event {i}: {evt['title']}")
        print(f"Date    : {evt['date']}")
        print(f"Links   : {evt['links']}")
        print(f"Text preview:\n{evt['raw_text'][:800]}")
