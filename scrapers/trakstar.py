"""
Trakstar (ATS) job board scraper — used by Apollo, Fortis, and other hospitals.
URLs:
    https://naukriapollo.hire.trakstar.com/jobs
    https://fortishealthcare.hire.trakstar.com/jobs

Trakstar is a single-page React app. We use Playwright to load jobs then
extract the JSON state. Each hospital has its own branded board.
"""
from __future__ import annotations
import json
import logging
import re
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import make_lead, is_target_city, split_city_area
from .browser import BrowserClient, PlaywrightNotInstalledError

logger = logging.getLogger(__name__)

BOARDS = [
    {
        "url": "https://naukriapollo.hire.trakstar.com/jobs",
        "hospital": "Apollo Hospital",
    },
    {
        "url": "https://fortishealthcare.hire.trakstar.com/jobs",
        "hospital": "Fortis Hospital",
    },
]

WAIT_SELECTOR = "a[href*='/jobs/'], .job-listing, [data-job-id]"


def _extract_json_state(html: str) -> List[dict]:
    """Trakstar sometimes embeds job data in a window.__INITIAL_STATE__ or similar."""
    m = re.search(r'window\.__[A-Z_]+\s*=\s*(\{.+?\});', html, re.DOTALL)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def _parse_card(card, *, hospital: str, base_url: str) -> dict | None:
    # Trakstar: <a href="/jobs/123-job-title">Job Title</a>
    href = card.get("href", "") if card.name == "a" else (card.select_one("a[href*='/jobs/']") or {}).get("href", "")
    if not href:
        return None
    url = urljoin(base_url, href)

    title_el = card.select_one("h2, h3, .job-title, [class*='title']") if hasattr(card, "select_one") else None
    if title_el is None and card.name == "a":
        title = card.get_text(strip=True)
    else:
        title = title_el.get_text(strip=True) if title_el else card.get_text(strip=True) if hasattr(card, "get_text") else "Medical Staff"
    if not title or len(title) < 3:
        return None

    loc_el = card.select_one("[class*='location']") if hasattr(card, "select_one") else None
    loc = loc_el.get_text(strip=True) if loc_el else "Bengaluru"
    city, area = split_city_area(loc)

    try:
        return make_lead(
            hospital=hospital,
            role=title,
            city=city,
            area=area,
            source_url=url,
        )
    except ValueError:
        return None


async def _scrape_board(url: str, hospital: str) -> List[dict]:
    try:
        async with BrowserClient() as browser:
            html = await browser.fetch_html(url, wait_selector=WAIT_SELECTOR, wait_ms=4000)
    except PlaywrightNotInstalledError as e:
        logger.error(f"Trakstar {hospital} needs Playwright: {e}")
        return []
    soup = BeautifulSoup(html, "html.parser")
    leads: list = []
    for a in soup.select(WAIT_SELECTOR):
        lead = _parse_card(a, hospital=hospital, base_url=url)
        if lead and (is_target_city(lead["city"], lead["area"]) or True):  # Apollo/Fortis are all-IN
            leads.append(lead)
    return leads


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    """Scrape all Trakstar boards (Apollo + Fortis)."""
    all_leads: List[dict] = []
    for board in BOARDS:
        try:
            leads = await _scrape_board(board["url"], board["hospital"])
            # Filter for Bengaluru leads
            all_leads.extend([l for l in leads if is_target_city(l.get("city", ""), l.get("area", ""))])
        except Exception as e:
            logger.error(f"Trakstar {board['hospital']} failed: {e}")
    return all_leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"Trakstar (Apollo+Fortis): {len(out)} Bengaluru leads")
