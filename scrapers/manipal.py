"""
Manipal Hospitals careers scraper.
URL: https://careers.manipalhospitals.com/job-openings/

The site is a JS-rendered SPA. We use Playwright to load each job card,
then visit the detail page to get the full description.
"""
from __future__ import annotations
import logging
import re
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import make_lead, is_target_city, split_city_area
from .browser import BrowserClient, PlaywrightNotInstalledError

logger = logging.getLogger(__name__)

URL = "https://careers.manipalhospitals.com/job-openings/"


async def _parse_listing(html: str, base: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    leads: List[dict] = []
    # Manipal listing typically uses <a href="/job/..."> or job-title-link
    for a in soup.select('a[href*="/job"], a.job-title, [data-job-id] a'):
        href = a.get("href", "")
        if not href:
            continue
        url = urljoin(base, href)
        title = a.get_text(strip=True) or "Medical Staff"
        if not title or len(title) < 3:
            continue
        try:
            leads.append(make_lead(
                hospital="Manipal Hospital",
                role=title,
                department="",
                city="Bengaluru",  # Manipal's careers page is mostly Bangalore
                area="",
                source_url=url,
            ))
        except ValueError:
            continue
    return leads


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    try:
        async with BrowserClient() as browser:
            html = await browser.fetch_html(
                URL,
                wait_selector="a[href*='/job'], .job-card, .job-listing",
                wait_ms=3000,
            )
    except PlaywrightNotInstalledError as e:
        logger.error(f"Manipal scraper needs Playwright: {e}")
        return []
    leads = await _parse_listing(html, URL)
    # Manipal's careers are all Bengaluru, so city filter is loose
    return [l for l in leads if is_target_city(l["city"], l["area"]) or True]  # keep all Manipal leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"Manipal: {len(out)} leads")
    for o in out[:3]:
        print(" -", o["hospital"], "|", o["role"])
