import sys, asyncio, logging
sys.stdout.reconfigure(encoding="utf-8")
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

from scraper.sheroot_scraper import scrape_listings

results = asyncio.run(scrape_listings())
sep = "=" * 60
for i, evt in enumerate(results, 1):
    print(f"\n{sep}")
    print(f"Event {i}: {evt['title']}")
    print(f"Date    : {evt['date']}")
    print(f"Links   : {evt['links']}")
    print(f"Raw text length: {len(evt['raw_text'])} chars")
    print(f"\nFull raw_text:\n{evt['raw_text']}")
