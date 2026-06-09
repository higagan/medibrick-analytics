"""
Backfill repost_count for existing leads (set to 1 since we can't recover
deleted duplicate counts). Future scrapes will compute repost_count automatically.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client


def main():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        print("❌ SUPABASE_URL and SUPABASE_KEY must be set")
        return

    client = create_client(url, key)

    # Fetch all leads
    resp = client.table("leads").select("id").execute()
    leads = resp.data or []
    print(f"Updating {len(leads)} leads with repost_count=1...")

    updated = 0
    for lead in leads:
        try:
            client.table("leads").update({"repost_count": 1}).eq("id", lead["id"]).execute()
            updated += 1
        except Exception as e:
            print(f"  Failed to update {lead['id']}: {e}")

    print(f"Done. Updated {updated}/{len(leads)} leads.")


if __name__ == "__main__":
    main()
