"""
Run all 10 lead sources in sequence, dedupe, and push to Supabase.

Usage:
    python -m scrapers.run_all              # scrape + push
    python -m scrapers.run_all --dry-run    # scrape only, print summary
    python -m scrapers.run_all --only docthub  # one source
    python -m scrapers.run_all --skip indeed    # all except one
    python -m scrapers.run_all --city Mumbai   # override target city

Reads SUPABASE_URL and SUPABASE_KEY from .env (same vars the FastAPI app uses).
"""
from __future__ import annotations
import argparse
import asyncio
import importlib
import logging
import os
import sys
import time
from typing import List, Dict

# Allow `python -m scrapers.run_all` from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env (for SUPABASE_URL/SUPABASE_KEY)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scrapers.run_all")


# 10 scraper modules - one per source. The brief had:
# 4 Indeed + 1 Manipal + 1 DoctHub + 1 DrLogy + 1 Foundit + 1 Apollo Trakstar + 1 Fortis Trakstar + 1 JobHai = 11 URLs
# We map each to a module.
SOURCES = {
    "indeed":    "scrapers.indeed",
    "manipal":   "scrapers.manipal",
    "docthub":   "scrapers.docthub",
    "drlogy":    "scrapers.drlogy",
    "foundit":   "scrapers.foundit",
    "trakstar":  "scrapers.trakstar",   # covers Apollo + Fortis
    "jobhai":    "scrapers.jobhai",
}


async def run_source(name: str, module_path: str, target_city: str) -> List[dict]:
    """Run a single scraper module. Returns its leads list."""
    t0 = time.time()
    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        logger.error(f"[{name}] Failed to import: {e}")
        return []
    try:
        leads = await mod.scrape(target_city=target_city)
    except Exception as e:
        logger.exception(f"[{name}] Scrape failed: {e}")
        return []
    dt = time.time() - t0
    logger.info(f"[{name}] {len(leads)} leads in {dt:.1f}s")
    return leads


def dedupe(leads: List[dict]) -> List[dict]:
    """Dedupe by source_url. Keeps first occurrence."""
    seen: set = set()
    out: list = []
    for lead in leads:
        url = lead.get("source_url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(lead)
    return out


async def main():
    parser = argparse.ArgumentParser(description="Run all 10 lead scrapers and push to Supabase")
    parser.add_argument("--dry-run", action="store_true", help="Scrape + dedupe only, don't push")
    parser.add_argument("--only", nargs="*", help="Run only these source names (space-separated)")
    parser.add_argument("--skip", nargs="*", default=[], help="Skip these source names")
    parser.add_argument("--city", default="Bengaluru", help="Target city (default: Bengaluru)")
    parser.add_argument("--push", action="store_true", help="Force push even if --dry-run is not set")
    args = parser.parse_args()

    only = set(args.only) if args.only else None
    skip = set(args.skip)

    selected = []
    for name, module in SOURCES.items():
        if only and name not in only:
            continue
        if name in skip:
            continue
        selected.append((name, module))

    if not selected:
        logger.error("No sources selected")
        return 1

    logger.info(f"Running {len(selected)} source(s): {[n for n, _ in selected]}")

    # Run all sources concurrently with a small stagger
    tasks = []
    for i, (name, module) in enumerate(selected):
        await asyncio.sleep(i * 0.5)  # stagger to be nice
        tasks.append((name, asyncio.create_task(run_source(name, module, args.city))))

    # Wait for all
    all_leads: List[dict] = []
    for name, task in tasks:
        try:
            leads = await task
            all_leads.extend(leads)
        except Exception as e:
            logger.error(f"[{name}] {e}")

    raw_count = len(all_leads)
    all_leads = dedupe(all_leads)
    logger.info(f"Total: {raw_count} raw → {len(all_leads)} unique leads")

    # Print summary
    by_source: Dict[str, int] = {}
    for lead in all_leads:
        s = lead.get("source", "Unknown")
        by_source[s] = by_source.get(s, 0) + 1
    print("\n" + "=" * 50)
    print(f"LEAD SUMMARY ({len(all_leads)} total)")
    print("=" * 50)
    for s, n in sorted(by_source.items(), key=lambda x: -x[1]):
        print(f"  {s:15} {n:>4}")
    print()

    if args.dry_run:
        logger.info("--dry-run set; skipping Supabase push")
        return 0

    # Push to Supabase
    if not args.dry_run or args.push:
        from scrapers.push_to_supabase import push_leads
        result = push_leads(all_leads)
        print(f"\nPushed to Supabase: {result}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()) or 0)
