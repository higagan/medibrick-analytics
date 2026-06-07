"""
Scrapers package — runs on the user's laptop, writes to Supabase.

Each scraper module exposes a `scrape() -> list[dict]` function that returns
normalized lead dicts matching the Supabase `leads` table schema.

Field contract (must match /static/leads.html UI + Supabase `leads` columns):
    hospital, role, department, city, area, salary, hiring_type,
    phone, email, contact, notes, date_posted, source_url, source, llm_data
"""
