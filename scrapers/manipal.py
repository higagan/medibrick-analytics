"""
Manipal Hospitals careers scraper.
URL: https://careers.manipalhospitals.com/job-openings/

NOTE: As of 2026-06, this site times out from headless Chromium (likely
blocks cloud / datacenter IPs). Returning empty list with a clear log.
The same jobs are scraped via Indeed (e.g. "Manipal Hospital - Millers Road"
in our test data). Re-enable if you find a working approach (e.g. residential
proxy).
"""
from __future__ import annotations
import logging
from typing import List

from .base import make_lead

logger = logging.getLogger(__name__)


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    logger.warning(
        "Manipal scraper is disabled: careers.manipalhospitals.com times out "
        "from headless browsers. Use Indeed (already in run_all) to get Manipal jobs."
    )
    return []


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"Manipal: {len(out)} leads (disabled)")
