"""
JobHai job board scraper.
URL: https://www.jobhai.com/max-healthcare-jobs-cmp

JobHai is a JS-heavy site. We use Playwright to load the listing and extract
Max Healthcare (or any company) job cards.
"""
from __future__ import annotations
import logging
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import make_lead, is_target_city, split_city_area
from .browser import BrowserClient, PlaywrightNotInstalledError

logger = logging.getLogger(__name__)

URL = "https://www.jobhai.com/max-healthcare-jobs-cmp"
WAIT_SELECTOR = "a[href*='/job/'], .job-card, [class*='jobCard']"
MAX_PAGES = 3


def _parse_card(card, base_url: str) -> dict | None:
    href = card.get("href", "") if card.name == "a" else (card.select_one("a[href*='/job/']") or {}).get("href", "")
    if not href:
        return None
    url = urljoin(base_url, href)

    title_el = card.select_one("h2, h3, .job-title, [class*='title']") if hasattr(card, "select_one") else None
    if title_el:
        title = title_el.get_text(strip=True)
    else:
        title = card.get_text(strip=True) if hasattr(card, "get_text") else "Medical Staff"
    if not title or len(title) < 3:
        return None

    company_el = card.select_one("[class*='company']") if hasattr(card, "select_one") else None
    company = company_el.get_text(strip=True) if company_el else "Max Healthcare"

    loc_el = card.select_one("[class*='location']") if hasattr(card, "select_one") else None
    loc = loc_el.get_text(strip=True) if loc_el else "Bengaluru"
    city, area = split_city_area(loc)

    salary_el = card.select_one("[class*='salary']") if hasattr(card, "select_one") else None
    salary = salary_el.get_text(strip=True) if salary_el else None

    try:
        return make_lead(
            hospital=company,
            role=title,
            city=city,
            area=area,
            salary=salary,
            source_url=url,
        )
    except ValueError:
        return None


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    try:
        async with BrowserClient() as browser:
            pages = await browser.fetch_all_pages(
                URL,
                wait_selector=WAIT_SELECTOR,
                next_button_selector="a[aria-label*='Next'], button[aria-label*='Next']",
                max_pages=MAX_PAGES,
            )
    except PlaywrightNotInstalledError as e:
        logger.error(f"JobHai scraper needs Playwright: {e}")
        return []
    leads: list = []
    for html in pages:
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select(WAIT_SELECTOR):
            lead = _parse_card(card, base_url=URL)
            if lead and is_target_city(lead["city"], lead["area"]):
                leads.append(lead)
    return leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"JobHai: {len(out)} Bengaluru leads")
