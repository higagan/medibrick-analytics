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
    type_label_re = re.compile(r"^(full[-\s]?time|part[-\s]?time|contract|temporary|permanent|internship|freelance|walk[-\s]?in|locum)(\s*\+\d+)?$", re.IGNORECASE)
    currency_re = re.compile(r"[₹$€£]|lakh|per\s+(month|year|hour|week|day|shift|annum)", re.IGNORECASE)
    has_digits = lambda s: any(c.isdigit() for c in s)

    # Collect all attribute snippets, then classify each
    salary_el = card.select_one("[data-testid='attribute_snippet_testid'], .salary-snippet, .salaryText")
    snippets = []
    if salary_el:
        snippets.append(salary_el.get_text(strip=True))
    for extra in card.select("[data-testid='attribute_snippet_testid']"):
        txt = extra.get_text(strip=True)
        if txt and txt not in snippets:
            snippets.append(txt)

    for txt in snippets:
        if not txt:
            continue
        if type_label_re.match(txt):
            # It's a job-type label (possibly with "+N" shifts)
            if not hiring_type:
                hiring_type = txt
        elif has_digits(txt) and (currency_re.search(txt) or len(txt) > 8):
            # Real salary: contains digits AND currency symbol / "per X" / longer string
            if not salary:
                salary = txt
        elif has_digits(txt) and not hiring_type and not txt.startswith("+"):
            # Numeric-only thing - might be experience, prefer as hiring_type
            hiring_type = txt

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
            # Indeed paginates with &start=N. Fetch each page directly.
            page_urls = [url] + [
                f"{url}{'&' if '?' in url else '?'}start={i*10}"
                for i in range(1, MAX_PAGES)
            ]
            for page_url in page_urls:
                try:
                    html = await browser.fetch_html(
                        page_url,
                        wait_selector=WAIT_SELECTOR,
                        wait_ms=2000,
                        timeout_ms=20000,
                    )
                    soup = BeautifulSoup(html, "html.parser")
                    cards = soup.select(WAIT_SELECTOR)
                    for card in cards:
                        lead = _parse_card(card, base_url=url)
                        if lead and is_target_city(lead["city"], lead["area"]):
                            leads.append(lead)
                except Exception as e:
                    logger.warning(f"Indeed page {page_url}: {e}")
                    continue
    except PlaywrightNotInstalledError as e:
        logger.error(f"Indeed scraper needs Playwright: {e}")
        return []
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
