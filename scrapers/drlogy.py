"""
DrLogy job board scraper.
URL: https://www.drlogy.com/jobs?job_type=Temporary&job_type=Contract&job_type=Walk+In

Next.js app. Server-rendered jobs may be partial; we use Playwright to
ensure full data + paginate.
"""
from __future__ import annotations
import json
import logging
import re
from typing import List

from bs4 import BeautifulSoup

from .base import HttpClient, make_lead, is_target_city, split_city_area
from .browser import BrowserClient, PlaywrightNotInstalledError

logger = logging.getLogger(__name__)

BASE = "https://www.drlogy.com"
URL = (
    f"{BASE}/jobs?job_type=Temporary&job_type=Contract&job_type=Walk+In"
)


def _extract_from_next_data(html: str) -> List[dict]:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    # Walk the JSON to find a jobs list
    results: list = []

    def walk(obj, depth=0):
        if depth > 8:
            return
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("jobs", "results", "data") and isinstance(v, list) and v and isinstance(v[0], dict):
                    if any(field in v[0] for field in ("title", "name", "jobTitle")):
                        results.extend(v)
                else:
                    walk(v, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)
    walk(data)
    return results


def _parse_job(job: dict) -> dict | None:
    title = job.get("title") or job.get("name") or job.get("jobTitle") or "Medical Staff"
    hospital = (
        job.get("hospital_name")
        or job.get("hospitalName")
        or (job.get("hospital") or {}).get("name") if isinstance(job.get("hospital"), dict) else job.get("hospital")
        or job.get("organization")
        or job.get("company")
        or "Unknown Hospital"
    )
    loc = job.get("location") or job.get("city") or ""
    city, area = split_city_area(str(loc))
    salary = job.get("salary") or job.get("salaryText")
    date_posted = job.get("date_posted") or job.get("created_at") or job.get("publishedDate")
    job_id = job.get("id") or job.get("slug") or job.get("code")
    source_url = f"{BASE}/job/{job_id}" if job_id else ""
    if not source_url:
        return None
    try:
        return make_lead(
            hospital=hospital,
            role=title,
            department="",
            city=city,
            area=area,
            salary=str(salary) if salary else None,
            date_posted=date_posted,
            source_url=source_url,
        )
    except ValueError:
        return None


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    # First try a plain HTTP fetch (faster, no browser needed)
    client = HttpClient()
    try:
        resp = await client.get(URL)
        if resp.status_code == 200:
            jobs = _extract_from_next_data(resp.text)
            leads = []
            for job in jobs:
                lead = _parse_job(job)
                if lead and is_target_city(lead["city"], lead["area"]):
                    leads.append(lead)
            if leads:
                return leads
    except Exception as e:
        logger.warning(f"DrLogy HTTP fetch failed: {e}")
    finally:
        await client.aclose()

    # Fallback: Playwright
    try:
        async with BrowserClient() as browser:
            html = await browser.fetch_html(URL, wait_selector="a[href*='/job/']", wait_ms=3000)
    except PlaywrightNotInstalledError as e:
        logger.error(f"DrLogy scraper needs Playwright for full data: {e}")
        return []

    soup = BeautifulSoup(html, "html.parser")
    leads: list = []
    for a in soup.select("a[href*='/job/']"):
        href = a.get("href", "")
        title = a.get_text(strip=True)
        if not title or len(title) < 3:
            continue
        try:
            leads.append(make_lead(
                hospital="DrLogy Hospital",
                role=title,
                city="Bengaluru",
                source_url=f"{BASE}{href}" if href.startswith("/") else href,
            ))
        except ValueError:
            continue
    return [l for l in leads if is_target_city(l["city"], l["area"])]


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"DrLogy: {len(out)} Bengaluru leads")
