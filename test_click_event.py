"""
Test clicking the Feb 27 calendar event to see what page/file loads.
Intercepts all responses so we can tell if it opens HTML or a PDF.
"""
import asyncio
import sys
import logging
from playwright.async_api import async_playwright

sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

SHEROOT_URL = "https://www.sheroot.co.za/fixed-property-sales.html"
TARGET_DATE = "27"  # Feb 27 event


async def test_click():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)  # visible so we can see what happens
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # Track all responses
        responses_seen = []

        async def on_response(resp):
            url = resp.url
            ct = resp.headers.get("content-type", "")
            responses_seen.append((url, ct, resp.status))
            if "pdf" in ct.lower() or url.lower().endswith(".pdf"):
                logger.info("PDF RESPONSE: %s  content-type=%s", url, ct)
            elif "Listfixed" in url or "sheroot" in url.lower():
                logger.info("Sheroot response: %s  status=%s  content-type=%s", url, resp.status, ct)

        page.on("response", on_response)

        # Also track new pages opened (newtab links)
        new_pages = []

        async def on_page(new_page):
            logger.info("NEW TAB opened: %s", new_page.url)
            new_pages.append(new_page)
            new_page.on("response", on_response)

        context.on("page", on_page)

        logger.info("Loading Sheroot page...")
        await page.goto(SHEROOT_URL, timeout=60_000, wait_until="networkidle")

        # Scroll to the iframe to trigger lazy rendering
        iframe_el = await page.query_selector("iframe")
        if iframe_el:
            await iframe_el.scroll_into_view_if_needed()
            logger.info("Scrolled to iframe.")
        await asyncio.sleep(10)  # give React more time to render

        # Find the iframe
        frames = page.frames
        logger.info("Frames on page: %d", len(frames))
        for f in frames:
            logger.info("  Frame URL: %s", f.url)

        iframe = next(
            (f for f in frames if "inffuse" in f.url or "calendar" in f.url.lower()),
            None,
        )
        if not iframe:
            logger.warning("Could not find Inffuse iframe. Trying all frames...")
            iframe = frames[1] if len(frames) > 1 else None

        if not iframe:
            logger.error("No iframe found.")
            await browser.close()
            return

        logger.info("Using iframe: %s", iframe.url)

        # Dump full iframe body text to see what's rendered
        try:
            body_text = await iframe.inner_text("body")
            logger.info("Iframe body text (first 1000 chars):\n%s", body_text[:1000])
        except Exception as e:
            logger.warning("Could not read iframe body: %s", e)

        # Pierce Shadow DOM on #root to read calendar content
        try:
            shadow_html = await iframe.evaluate(
                "() => document.querySelector('#root')?.shadowRoot?.innerHTML || 'NO SHADOW ROOT'"
            )
            logger.info("Shadow DOM innerHTML (first 4000 chars):\n%s", shadow_html[:4000])
        except Exception as e:
            logger.warning("Could not read Shadow DOM: %s", e)

        # Click the event via Playwright's shadow-piercing text selector
        clicked = False
        for selector in [
            "text=Sale in Execution",
            "text=Sale",
        ]:
            try:
                el = await iframe.wait_for_selector(selector, timeout=5_000)
                logger.info("Clicking selector '%s'...", selector)
                await el.click()
                clicked = True
                break
            except Exception:
                continue

        if not clicked:
            logger.warning("Could not find event card.")
            await browser.close()
            return

        logger.info("Clicked. Waiting for popup...")
        await asyncio.sleep(8)

        # Get full text content of Shadow DOM (strips all HTML tags)
        try:
            shadow_text = await iframe.evaluate(
                "() => document.querySelector('#root')?.shadowRoot?.textContent || 'NO SHADOW ROOT'"
            )
            logger.info("Shadow DOM full text after click:\n%s", shadow_text)
        except Exception as e:
            logger.warning("Could not read Shadow DOM text: %s", e)

        # Also get innerHTML but look specifically for popup/modal/details elements
        try:
            popup_html = await iframe.evaluate("""() => {
                const root = document.querySelector('#root')?.shadowRoot;
                if (!root) return 'NO SHADOW ROOT';
                const popup = root.querySelector('[class*=\"popup\"], [class*=\"modal\"], [class*=\"detail\"], [class*=\"event-detail\"], [class*=\"popover\"]');
                if (popup) return popup.innerHTML;
                return 'NO POPUP ELEMENT FOUND. All classes: ' +
                    Array.from(root.querySelectorAll('[class]')).map(el => el.className).join(' | ');
            }""")
            logger.info("Popup/detail element:\n%s", popup_html[:3000])
        except Exception as e:
            logger.warning("Could not find popup element: %s", e)

        # Check any new tabs opened by the "Click here for the list" link
        if new_pages:
            for np in new_pages:
                await asyncio.sleep(3)
                logger.info("New tab URL: %s", np.url)
                try:
                    body = await np.inner_text("body")
                    logger.info("New tab body (first 2000 chars):\n%s", body[:2000])
                except Exception as e:
                    logger.info("Could not get new tab text: %s", e)
        else:
            logger.info("No new tab opened after click.")

        logger.info("\n--- All Sheroot/Listfixed/PDF responses ---")
        for url, ct, status in responses_seen:
            if "sheroot" in url.lower() or "listfixed" in url.lower() or "pdf" in ct.lower():
                logger.info("  [%s] %s  (%s)", status, url, ct)

        await asyncio.sleep(3)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(test_click())
