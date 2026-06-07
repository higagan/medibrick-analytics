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
        results = response.data[0]["results"]
        # Migrate legacy "pharmacy" type to "ayush" (post-rebrand)
        for item in results:
            if item.get("type") == "pharmacy":
                item["type"] = "ayush"
        return results
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
        # Migrate legacy "pharmacy" type to "ayush" (post-rebrand)
        for item in existing:
            if item.get("type") == "pharmacy":
                item["type"] = "ayush"

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


async def list_leads(city: Optional[str] = None, area: Optional[str] = None, department: Optional[str] = None) -> List[dict]:
    """List all leads, optionally filtered by city/area/department (substring match)."""
    if not supabase:
        return []
    query = supabase.table("leads").select("*").order("created_at", desc=True)
    if city:
        query = query.ilike("city", f"%{city}%")
    if area:
        query = query.ilike("area", f"%{area}%")
    if department:
        # Match against title OR department (fuzzy)
        query = query.or_(f"title.ilike.%{department}%,department.ilike.%{department}%")
    response = query.execute()
    return response.data or []


async def create_lead(data: dict) -> dict:
    """Insert a new lead. Returns the inserted row."""
    if not supabase:
        return {"error": "Supabase not configured"}
    data["created_at"] = datetime.utcnow().isoformat()
    response = supabase.table("leads").insert(data).execute()
    return response.data[0] if response.data else {"error": "Insert failed"}


async def count_new_leads() -> int:
    """Count leads added in the last 24 hours (for the menu badge)."""
    if not supabase:
        return 0
    cutoff = (datetime.utcnow() - timedelta(hours=24)).isoformat()
    response = supabase.table("leads") \
        .select("id", count="exact") \
        .gte("created_at", cutoff) \
        .execute()
    return response.count or 0
