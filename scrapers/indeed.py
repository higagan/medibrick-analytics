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

    # Job link — grab jk from the card so the caller can pair with the
    # JSON-embedded createDate/formattedRelativeTime (the DOM doesn't render
    # the date; Indeed's frontend hydrates it from window.mosaic JSON).
    link_el = card.select_one("a[data-jk], h2.jobTitle a, a[href*='/viewjob']")
    href = link_el.get("href", "") if link_el else ""
    jk = link_el.get("data-jk", "") if link_el else ""
    if jk and not href:
        href = f"/viewjob?jk={jk}"
    source_url = urljoin(base_url, href) if href else f"https://in.indeed.com/jobs?q={title}"

    if not source_url:
        return None
    # date_posted is filled in by _scrape_one_url using the JSON-embedded
    # createDate/formattedRelativeTime, paired by card order.
    try:
        return make_lead(
            hospital=company,
            role=title,
            department="",
            city=city,
            area=area,
            salary=salary,
            hiring_type=hiring_type,
            date_posted=None,  # filled in later
            source_url=source_url,
        )
    except ValueError:
        return None


# Regex helpers for parsing the embedded JSON.
# The DOM doesn't render Indeed's post dates; the date is embedded in
# window.mosaic JSON. Each job object has 'jobkey' (16-char hex), then later
# 'createDate' (epoch ms) and 'formattedRelativeTime' (e.g. 'Just posted').
# We build a {jobkey: iso_date} map and look up each card by its data-jk.
_JK_RE = re.compile(r'"jobkey":"([a-f0-9]{16})"')
_DATE_TS_RE = re.compile(r'"createDate":(\d{13})')
_DATE_TEXT_RE = re.compile(r'"formattedRelativeTime":"([^"]+)"')


def _extract_indeed_dates_by_jk(html: str) -> dict[str, str]:
    """
    Build a {jobkey: 'YYYY-MM-DD'} map from Indeed's embedded window.mosaic JSON.
    The DOM doesn't render the date; Indeed hydrates it from JSON after
    page load. Cards are matched to dates by their data-jk attribute.
    """
    from datetime import datetime, timezone
    jks = _JK_RE.findall(html)
    ts_iter = list(_DATE_TS_RE.finditer(html))
    text_iter = list(_DATE_TEXT_RE.finditer(html))
    # Pair createDate and formattedRelativeTime by position (they appear
    # in the same order within the same script block).
    dates_by_pos: list[tuple[str, str]] = []  # (iso_date, raw_text)
    for ts_m, text_m in zip(ts_iter, text_iter):
        try:
            dt = datetime.fromtimestamp(int(ts_m.group(1)) / 1000, tz=timezone.utc)
            iso = dt.strftime("%Y-%m-%d")
        except (ValueError, OSError):
            iso = text_m.group(1)
        dates_by_pos.append((iso, text_m.group(1)))
    # Match each jobkey to a date by going forward from jobkey position and
    # finding the next createDate. This handles Indeed's nested JSON where
    # jks and createDates can be in different orders.
    out: dict[str, str] = {}
    for jk in jks:
        # Find the position of this jobkey in the HTML
        idx = html.find(f'"jobkey":"{jk}"')
        if idx == -1:
            continue
        # Find the next createDate at or after idx
        for ts_m in ts_iter:
            if ts_m.start() >= idx:
                # Find the corresponding formattedRelativeTime
                # by position-paired list
                pos = ts_iter.index(ts_m) if ts_m in ts_iter else -1
                if pos < len(dates_by_pos):
                    out[jk] = dates_by_pos[pos][0]
                else:
                    out[jk] = ""
                break
    return out


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
                    # Build a {jobkey: iso_date} map from the page's
                    # embedded window.mosaic JSON. Indeed doesn't render
                    # the post date in the DOM — it hydrates from JSON.
                    date_map = _extract_indeed_dates_by_jk(html)
                    for card in cards:
                        # Grab the jk from the card so we can look up its date
                        link_el = card.select_one("a[data-jk], a[href*='/viewjob']")
                        jk = (link_el.get("data-jk", "") if link_el else "") or ""
                        if not jk:
                            href = link_el.get("href", "") if link_el else ""
                            m = re.search(r"jk=([a-f0-9]+)", href)
                            if m:
                                jk = m.group(1)
                        lead = _parse_card(card, base_url=url)
                        if lead and is_target_city(lead["city"], lead["area"]):
                            if jk and jk in date_map:
                                lead["date_posted"] = date_map[jk]
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
