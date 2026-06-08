"""
DrLogy job board scraper.
URL: https://www.drlogy.com/jobs?job_type=Temporary&job_type=Contract&job_type=Walk+In

Next.js app. The job cards are rendered in HTML as <a href="/jobs/<city>/<slug>-<id>">
wrapping a card with title, hospital, city, experience, salary, last date, type.
We use Playwright to load the page, then parse the DOM.
"""
from __future__ import annotations
import json
import logging
import re
from typing import List
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import HttpClient, make_lead, is_target_city, split_city_area
from .browser import BrowserClient, PlaywrightNotInstalledError

logger = logging.getLogger(__name__)

BASE = "https://www.drlogy.com"
# City-filtered URLs (best for finding Bengaluru jobs)
URLS = [
    f"{BASE}/jobs/bangalore",
    f"{BASE}/jobs/bengaluru",
    # Fallback: general filter
    f"{BASE}/jobs?job_type=Temporary&job_type=Contract&job_type=Walk+In",
]
# Backward compat for tests
URL = URLS[0]


def _parse_card_text(card_text: str, href: str) -> dict | None:
    """
    Parse the text content of one DrLogy card.
    Format: 'Title\nHospitalName\nCity\nExperience\nSalary\nLastDate\nFull time'
    """
    parts = [p.strip() for p in card_text.split("\n") if p.strip()]
    if len(parts) < 3:
        return None
    title = parts[0]
    hospital = parts[1] if not parts[1].lower() in ("apply now", "view details") else "Unknown Hospital"
    city_raw = parts[2] if len(parts) > 2 else ""
    # City is in the URL path: /jobs/<city>/<slug>-<id>
    m = re.search(r"/jobs/([^/]+)/", href)
    if m:
        city_raw = m.group(1).replace("-", " ").title()
    city, area = split_city_area(city_raw)
    # Salary is usually "Not disclosed" or a number
    salary = None
    for p in parts:
        if re.search(r"\d+[,.]?\d*\s*(L|lakh|Lac|Cr|K)", p, re.IGNORECASE) or "₹" in p:
            salary = p
            break
        if p.lower() == "not disclosed":
            continue
    # Type
    hiring_type = ""
    for p in parts:
        pl = p.lower()
        if "full time" in pl or "part time" in pl or "walk" in pl:
            hiring_type = p
            break
    return {
        "title": title,
        "hospital": hospital,
        "city": city,
        "area": area,
        "salary": salary,
        "hiring_type": hiring_type,
    }


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    leads: List[dict] = []
    try:
        async with BrowserClient() as browser:
            for url in URLS:
                try:
                    html = await browser.fetch_html(
                        url,
                        wait_selector="a[href*='/jobs/']",
                        wait_ms=4000,
                    )
                except Exception as e:
                    logger.warning(f"DrLogy {url}: {e}")
                    continue
                if not html or len(html) < 5000:
                    logger.warning(f"DrLogy {url}: html too small ({len(html) if html else 0} bytes)")
                    continue
                page_leads = _parse_html(html)
                logger.info(f"DrLogy {url}: {len(page_leads)} Bengaluru leads from page")
                leads.extend(page_leads)
    except PlaywrightNotInstalledError as e:
        logger.error(f"DrLogy scraper needs Playwright: {e}")
        return []
    # Final dedupe by source_url
    seen = set()
    out = []
    for l in leads:
        if l["source_url"] not in seen:
            seen.add(l["source_url"])
            out.append(l)
    return out


def _parse_html(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    leads: List[dict] = []
    for a in soup.select("a[href*='/jobs/']"):
        href = a.get("href", "")
        # Skip nav links to category pages
        if not re.search(r"/jobs/[^/]+/[^/]+-\d+", href):
            continue
        card_text = a.get_text("\n", strip=True)
        parsed = _parse_card_text(card_text, href)
        if not parsed or not parsed.get("title"):
            continue
        url = urljoin(BASE, href)
        try:
            lead = make_lead(
                hospital=parsed["hospital"],
                role=parsed["title"],
                department="",
                city=parsed["city"],
                area=parsed["area"],
                salary=parsed["salary"],
                hiring_type=parsed["hiring_type"],
                source_url=url,
            )
            if is_target_city(lead["city"], lead["area"]):
                leads.append(lead)
        except ValueError:
            continue
    return leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"DrLogy: {len(out)} Bengaluru leads")
    for o in out[:5]:
        print(" -", o["hospital"], "|", o["role"], "|", o["city"])
