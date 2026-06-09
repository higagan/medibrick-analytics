"""
Push scraped leads to Supabase with dedup on source_url.

The `leads` table has a UNIQUE constraint on source_url, so we use Supabase's
upsert. Existing rows are updated, new ones are inserted.

We batch by 50 (Supabase PostgREST limit) and report counts.
"""
from __future__ import annotations
import os
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# Lazy import - the FastAPI app may import this too, so we need to handle the
# case where supabase isn't installed
try:
    from supabase import create_client, Client
    _HAS_SUPABASE = True
except ImportError:
    _HAS_SUPABASE = False

# LLM enrichment: classify role/department/hospital via local Ollama.
# Defaults to medgemma:4b (Google's medical-tuned Gemma 3). Override via
# OLLAMA_MODEL env var. Skips silently if Ollama is offline or model not
# loaded.
try:
    from .llm_enrich import enrich_batch
    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False


BATCH_SIZE = 50


def _get_client() -> Optional["Client"]:
    if not _HAS_SUPABASE:
        logger.error("supabase package not installed. Run: pip install supabase")
        return None
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        logger.error("SUPABASE_URL and SUPABASE_KEY must be set in .env")
        return None
    return create_client(url, key)


def _lead_key(lead: dict) -> str:
    """Normalize a dedup key from hospital + role + city + area."""
    parts = [
        (lead.get("hospital") or "").strip().lower(),
        (lead.get("role") or "").strip().lower(),
        (lead.get("city") or "").strip().lower(),
        (lead.get("area") or "").strip().lower(),
    ]
    return "|".join(parts)


def _score_lead(lead: dict) -> int:
    """Richness score: prefer leads with salary, date, and LLM data."""
    score = 0
    if lead.get("salary"):
        score += 2
    if lead.get("date_posted"):
        score += 2
    if lead.get("llm_data"):
        score += 1
    return score


def dedup_leads(leads: List[dict]) -> List[dict]:
    """Deduplicate by (hospital, role, city, area), keeping the richest.
    Sets repost_count on the best lead so the UI can show 🔥 actively hiring."""
    buckets: Dict[str, List[dict]] = {}
    for lead in leads:
        key = _lead_key(lead)
        buckets.setdefault(key, []).append(lead)

    result = []
    for group in buckets.values():
        # Pick the lead with the highest richness score
        best = max(group, key=_score_lead)
        # repost_count = how many times we saw this exact job (including the best one)
        best["repost_count"] = len(group)
        result.append(best)
    return result


def push_leads(leads: List[dict]) -> Dict[str, int]:
    """
    Upsert leads into Supabase. Returns dict with counts:
        {"inserted": N, "updated": M, "skipped": K, "errors": E}
    Note: Supabase upsert doesn't easily distinguish insert vs update, so we
    pre-fetch existing URLs and split.
    """
    if not leads:
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    # NEW: enrich with local LLM (medgemma:4b via Ollama) before pushing.
    # Skips silently if Ollama is offline or model not loaded.
    if _HAS_LLM:
        leads = enrich_batch(leads)

    # Deduplicate by (hospital, role, city, area) — same job posted on multiple
    # pages or reposted with different URLs. Keep the one with salary/date.
    leads = dedup_leads(leads)

    client = _get_client()
    if client is None:
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": len(leads)}

    # Pre-fetch existing source_urls to split insert vs update
    urls = [l["source_url"] for l in leads if l.get("source_url")]
    existing_urls: set = set()
    try:
        # Query in chunks (PostgREST URL length limit)
        for i in range(0, len(urls), 100):
            chunk = urls[i:i + 100]
            resp = client.table("leads").select("source_url").in_("source_url", chunk).execute()
            for row in (resp.data or []):
                existing_urls.add(row.get("source_url"))
    except Exception as e:
        logger.error(f"Pre-fetch existing URLs failed: {e}")
        return {"inserted": 0, "updated": 0, "skipped": 0, "errors": len(leads)}

    inserted = 0
    updated = 0
    errors = 0

    for i in range(0, len(leads), BATCH_SIZE):
        batch = leads[i:i + BATCH_SIZE]
        try:
            resp = client.table("leads").upsert(batch, on_conflict="source_url").execute()
            if resp.data:
                # All rows in batch succeeded
                for row in resp.data:
                    if row.get("source_url") in existing_urls:
                        updated += 1
                    else:
                        inserted += 1
            else:
                # No data returned - count as success
                inserted += len(batch)
        except Exception as e:
            logger.error(f"Batch {i//BATCH_SIZE + 1} failed: {e}")
            errors += len(batch)

    result = {
        "inserted": inserted,
        "updated": updated,
        "skipped": 0,
        "errors": errors,
    }
    logger.info(f"Push complete: {result}")
    return result


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Smoke test: read existing count
    client = _get_client()
    if client:
        resp = client.table("leads").select("id", count="exact").execute()
        print(f"Current leads in DB: {resp.count}")
