"""
Indeed India scraper — handles all 5 query URLs from the user's brief.

Indeed India uses heavy anti-bot (Cloudflare + custom). We MUST use a real
browser (Playwright) to render the search results, then parse the rendered
DOM for job cards.

Source URLs:
    https://in.indeed.com/jobs?q=duty+doctor
    https://in.indeed.com/jobs?q=bams+doctor
    https://in.indeed.com/jobs?q=locum
    https://in.indeed.com/jobs?q=resident+medical+officer
    (one extra per user brief)
"""
from __future__ import annotations
import re
import logging
from typing import List
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .base import HttpClient, make_lead, is_target_city, split_city_area
from .browser import BrowserClient, PlaywrightNotInstalledError

logger = logging.getLogger(__name__)

URLS = [
    "https://in.indeed.com/jobs?q=duty+doctor&l=Bengaluru",
    "https://in.indeed.com/jobs?q=bams+doctor&l=Bengaluru",
    "https://in.indeed.com/jobs?q=locum&l=Bengaluru",
    "https://in.indeed.com/jobs?q=resident+medical+officer&l=Bengaluru",
    "https://in.indeed.com/jobs?q=doctor&l=Bengaluru",
]

# Indeed India can serve up to ~50 results per search; we paginate to 3 pages max.
MAX_PAGES = 3
WAIT_SELECTOR = ".job_seen_beacon, .jobsearch-ResultsList > li, [data-testid='job-result']"


def _parse_card(card, base_url: str) -> dict | None:
    """Parse one Indeed job card (BS4 element) into a lead dict."""
    # Title
    title_el = card.select_one("h2.jobTitle span, .jobTitle a span, h2 a span")
    title = title_el.get_text(strip=True) if title_el else "Medical Staff"

    # Company
    company_el = card.select_one("[data-testid='company-name'], .companyName")
    company = company_el.get_text(strip=True) if company_el else "Unknown Hospital"

    # Location
    loc_el = card.select_one("[data-testid='text-location'], .companyLocation")
    loc = loc_el.get_text(strip=True) if loc_el else ""
    city, area = split_city_area(loc)

    # Salary: prefer the salary snippet, but skip non-salary values like
    # "Part-time", "Full-time", "Full-time+1" that Indeed often shows in
    # the same attribute area.
    salary = None
    hiring_type = ""
    salary_el = card.select_one("[data-testid='attribute_snippet_testid'], .salary-snippet, .salaryText")
    if salary_el:
        raw = salary_el.get_text(strip=True)
        # Salary contains digits or a currency symbol
        if any(c.isdigit() for c in raw) or any(s in raw for s in ["₹", "$", "€", "£", "Lakh", "Cr"]):
            salary = raw
        elif raw:
            # Looks like a job-type label (Part-time, Full-time, etc.)
            hiring_type = raw
            # Indeed often shows both. Try a second snippet for actual salary.
            for other in card.select("[data-testid='attribute_snippet_testid']"):
                txt = other.get_text(strip=True)
                if txt and txt != raw and (any(c.isdigit() for c in txt) or "₹" in txt):
                    salary = txt
                    break

    # Date
    date_el = card.select_one("[data-testid='myJobsState'], .date, date")
    date_posted = date_el.get_text(strip=True) if date_el else None

    # Job link
    link_el = card.select_one("a[data-jk], h2.jobTitle a, a[href*='/viewjob']")
    href = link_el.get("href", "") if link_el else ""
    jk = link_el.get("data-jk", "") if link_el else ""
    if jk and not href:
        href = f"/viewjob?jk={jk}"
    source_url = urljoin(base_url, href) if href else f"https://in.indeed.com/jobs?q={title}"

    if not source_url:
        return None
    try:
        return make_lead(
            hospital=company,
            role=title,
            department="",
            city=city,
            area=area,
            salary=salary,
            hiring_type=hiring_type,
            date_posted=date_posted,
            source_url=source_url,
        )
    except ValueError:
        return None


async def _scrape_one_url(url: str) -> List[dict]:
    leads: List[dict] = []
    try:
        async with BrowserClient() as browser:
            pages = await browser.fetch_all_pages(
                url,
                wait_selector=WAIT_SELECTOR,
                next_button_selector="a[aria-label*='Next'], button[aria-label*='Next']",
                max_pages=MAX_PAGES,
            )
    except PlaywrightNotInstalledError as e:
        logger.error(f"Indeed scraper needs Playwright: {e}")
        return []
    for html in pages:
        soup = BeautifulSoup(html, "html.parser")
        for card in soup.select(WAIT_SELECTOR):
            lead = _parse_card(card, base_url=url)
            if lead and is_target_city(lead["city"], lead["area"]):
                leads.append(lead)
    return leads


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    """Scrape all 5 Indeed query URLs."""
    all_leads: List[dict] = []
    for url in URLS:
        try:
            leads = await _scrape_one_url(url)
            all_leads.extend(leads)
        except Exception as e:
            logger.error(f"Indeed {url} failed: {e}")
    return all_leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"Indeed: {len(out)} Bengaluru leads")
    for o in out[:3]:
        print(" -", o["hospital"], "|", o["role"], "|", o["city"])
