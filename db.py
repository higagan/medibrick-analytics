import os
from supabase import create_client, Client
from typing import List, Optional
from datetime import datetime, timedelta

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client | None = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


async def get_cached_results(area: str) -> Optional[List[dict]]:
    """Fetch cached results from Supabase if fresh (< 24h)."""
    if not supabase:
        return None
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    response = supabase.table("area_cache") \
        .select("results") \
        .eq("area", area) \
        .gte("created_at", cutoff) \
        .execute()

    if response.data:
        return response.data[0]["results"]
    return None


async def save_results(area: str, results: List[dict]):
    """Upsert results into Supabase cache."""
    if not supabase:
        return
    supabase.table("area_cache").upsert({
        "area": area,
        "results": results,
        "created_at": datetime.utcnow().isoformat()
    }).execute()
