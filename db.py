import os
from supabase import create_client, Client
from typing import List, Optional
from datetime import datetime, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

CACHE_TTL_HOURS = 720  # 30 days


async def get_cached_results(area: str) -> Optional[List[dict]]:
    """Fetch cached results from Supabase if fresh (< 30 days)."""
    if not supabase:
        return None
    cutoff = (datetime.utcnow() - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    response = supabase.table("area_cache") \
        .select("results") \
        .eq("area", area) \
        .gte("created_at", cutoff) \
        .execute()

    if response.data:
        return response.data[0]["results"]
    return None


async def merge_and_save_results(area: str, new_results: List[dict]):
    """Merge new results with existing cache, keeping old + adding new."""
    if not supabase:
        return

    # Fetch existing results (no TTL check — we want to merge even if stale)
    existing_response = supabase.table("area_cache") \
        .select("results") \
        .eq("area", area) \
        .execute()

    existing = []
    if existing_response.data:
        existing = existing_response.data[0]["results"]

    # Deduplicate by name, prefer new data
    seen = set()
    merged = []

    # Add new results first (they take priority)
    for item in new_results:
        name = item.get("name", "").strip().lower()
        if name and name not in seen:
            seen.add(name)
            merged.append(item)

    # Add existing results that aren't in the new set
    for item in existing:
        name = item.get("name", "").strip().lower()
        if name and name not in seen:
            seen.add(name)
            merged.append(item)

    supabase.table("area_cache").upsert({
        "area": area,
        "results": merged,
        "created_at": datetime.utcnow().isoformat()
    }).execute()
