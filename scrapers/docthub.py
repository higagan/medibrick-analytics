"""
DoctHub scraper — https://jobs.docthub.com/all-jobs
Next.js app; full job list is embedded in `__NEXT_DATA__` JSON.
We also build the right URL params to filter Bengaluru + locum/contract/temp.
"""
from __future__ import annotations
import json
import re
from typing import List

from .base import HttpClient, make_lead, is_target_city, split_city_area


# Search query URLs from user's brief (10 source URLs); DoctHub provides 1.
URL = "https://jobs.docthub.com/all-jobs?job_type%5B%5D=locum&job_type%5B%5D=temporary&job_type%5B%5D=contract"
# Also try a Bengaluru-only search by including a generic query string
BENGALURU_URL = (
    "https://jobs.docthub.com/all-jobs?city%5B%5D=bengaluru&city%5B%5D=bangalore"
)


def _parse_card(job: dict) -> dict | None:
    title = job.get("title") or "Medical Staff"
    org = (job.get("organization") or {}).get("name") or "Unknown Hospital"
    addr = (job.get("organization") or {}).get("address") or {}
    loc = job.get("location") or {}
    city_field = addr.get("city") or (loc.get("location") or "")
    area_field = loc.get("location") or ""
    # If loc.location already contains both (e.g. "South Delhi, Delhi"), prefer addr.city as city
    city, area = split_city_area(city_field)
    if area_field and area_field != city and not area:
        # area is 'area, city' style
        a2, _c2 = split_city_area(area_field)
        if a2 and a2.lower() != city.lower():
            area = a2
    salary = job.get("salary") or {}
    sal_min = salary.get("minAmount")
    sal_max = salary.get("maxAmount")
    sal_type = (salary.get("type") or "Monthly").lower()
    sal_str = None
    if sal_min and sal_max:
        sal_str = f"₹{sal_min:,} - ₹{sal_max:,} {sal_type}"
    elif sal_min:
        sal_str = f"₹{sal_min:,} {sal_type}"

    hiring_type = job.get("employementType") or ""
    date_posted = job.get("publishedDate") or job.get("createdDate")
    code = job.get("code") or job.get("id")
    source_url = f"https://jobs.docthub.com/job/{code}" if code else ""

    try:
        return make_lead(
            hospital=org,
            role=title,
            department="",  # DoctHub doesn't expose department separately
            city=city,
            area=area,
            salary=sal_str,
            hiring_type=hiring_type,
            date_posted=date_posted,
            source_url=source_url,
        )
    except ValueError:
        return None


def _extract_jobs_from_html(html: str) -> List[dict]:
    """Find __NEXT_DATA__ JSON and return its job list."""
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.+?)</script>', html, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    jobs_data = data.get("props", {}).get("pageProps", {}).get("jobsDataFromServer", {})
    return jobs_data.get("jobs") or []


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    """Scrape all DoctHub jobs (Next.js JSON). Filters by target city at the end."""
    client = HttpClient()
    leads: List[dict] = []
    try:
        # Hit both the locum-focused URL and the Bengaluru URL to maximize coverage
        for url in (URL, BENGALURU_URL):
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    continue
                jobs = _extract_jobs_from_html(resp.text)
                for job in jobs:
                    lead = _parse_card(job)
                    if lead and is_target_city(lead["city"], lead["area"]):
                        leads.append(lead)
            except Exception as e:
                print(f"[docthub] {url}: {e}")
    finally:
        await client.aclose()
    return leads


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"DoctHub: {len(out)} Bengaluru leads")
    for o in out[:3]:
        print(" -", o["hospital"], "|", o["role"], "|", o["city"])
