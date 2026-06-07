"""
JobHai job board scraper.
URL: https://www.jobhai.com/max-healthcare-jobs-cmp

NOTE: As of 2026-06, this site times out from headless Chromium.
Returning empty list with a clear log.
"""
from __future__ import annotations
import logging
from typing import List

logger = logging.getLogger(__name__)


async def scrape(target_city: str = "Bengaluru") -> List[dict]:
    logger.warning(
        "JobHai scraper is disabled: www.jobhai.com times out from headless browsers."
    )
    return []


if __name__ == "__main__":
    import asyncio
    out = asyncio.run(scrape())
    print(f"JobHai: {len(out)} leads (disabled)")
