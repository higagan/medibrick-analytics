"""Re-classify all existing leads through the LLM. Run once after deploy.

Usage: source venv/bin/activate && python reclassify.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

from db import supabase
from scrapers.llm_enrich import enrich_lead, is_available

if not is_available():
    print("Ollama not reachable. Start it first.")
    raise SystemExit(1)

print("Fetching leads...")
r = supabase.table('leads').select('*').execute()
todo = [l for l in r.data if not l.get('llm_data')]
print(f"Total: {len(r.data)}, to enrich: {len(todo)}")

if not todo:
    print("Nothing to do. All leads already enriched.")
    raise SystemExit(0)

n_ok = 0
for i, l in enumerate(todo):
    e = enrich_lead(l)
    if e.get('llm_data'):
        supabase.table('leads').update({
            'llm_data': e['llm_data'],
        }).eq('source_url', l['source_url']).execute()
        n_ok += 1
    if (i + 1) % 25 == 0:
        print(f"  {i+1}/{len(todo)}...")

print(f"Done. {n_ok}/{len(todo)} leads enriched.")
