"""
Foundit (formerly Monster India) job search scraper.
URL: https://www.foundit.in/srp/results?query=locum+doctor&locations=India

Angular SPA - requires Playwright to render job cards.
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

URL = "https://www.foundit.in/srp/results?query=locum+doctor&locations=Bengaluru"
MAX_PAGES = 3
WAIT_SELECTOR = ".cardContainer, [class*='jobCard']"


def _parse_card(card) -> dict | None:
    # Foundit card: title in .jobTitle or h3, company in .companyName
    title_el = card.select_one(".jobTitle, h3, [class*='title']")
    title = title_el.get_text(strip=True) if title_el else "Medical Staff"
    if not title or len(title) < 3:
        return None

    company_el = card.select_one(".companyName, [class*='company']")
    company = company_el.get_text(strip=True) if company_el else "Unknown Hospital"

    loc_el = card.select_one("[class*='location']")
    loc = loc_el.get_text(strip=True) if loc_el else "Bengaluru"
    city, area = split_city_area(loc)

    salary_el = card.select_one("[class*='salary']")
    salary = salary_el.get_text(strip=True) if salary_el else None

    date_el = card.select_one("[class*='date'], .bodyRow")
    date_posted = date_el.get_text(strip=True) if date_el else None

    link_el = card.select_one("a[href*='/job/'], a[href*='jobId']")
    href = link_el.get("href", "") if link_el else ""
    if href and not href.startswith("http"):
        href = urljoin("https://www.foundit.in", href)
    if not href:
        return None

    try:
        return make_lead(
            hospital=company,
            role=title,
            city=city,
            area=area,
            salary=salary,
            date_posted=date_posted,
            source_url=href,
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
        logger.error(f"Foundit scraper needs Playwright: {e}")
        return []
    leads: list = []
    for html in pages:
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select(WAIT_SELECTOR):
            lead = _parse_card(card)
            if lead and is_target_city(lead["city"], lead["area"]):
                leads.append(lead)
    return leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"Foundit: {len(out)} Bengaluru leads")
