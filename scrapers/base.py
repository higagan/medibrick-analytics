"""Shared utilities for all scrapers."""
from __future__ import annotations
import re
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

import httpx


# Cities we care about. Scrapers that paginate server-rendered lists will get
# global results; we filter here so only Bengaluru leads reach the DB.
TARGET_CITIES = {"bangalore", "bengaluru"}


# Source label from URL hostname. Mirrors /static/leads.html deriveSource().
SOURCE_LABELS = {
    "indeed.com": "Indeed",
    "in.indeed.com": "Indeed",
    "docthub.com": "DoctHub",
    "jobs.docthub.com": "DoctHub",
    "drlogy.com": "DrLogy",
    "www.drlogy.com": "DrLogy",
    "foundit.in": "Foundit",
    "www.foundit.in": "Foundit",
    "monster.com": "Foundit",
    "jobhai.com": "JobHai",
    "www.jobhai.com": "JobHai",
    "manipalhospitals.com": "Manipal",
    "careers.manipalhospitals.com": "Manipal",
    "trakstar.com": "Trakstar",
    "naukriapollo.hire.trakstar.com": "Apollo",
    "fortishealthcare.hire.trakstar.com": "Fortis",
    "linkedin.com": "LinkedIn",
    "naukri.com": "Naukri",
}


def derive_source_label(url: str) -> str:
    """Return a short display label for a source URL (Indeed, DoctHub, etc.)."""
    if not url:
        return "Web"
    try:
        host = urlparse(url).hostname or ""
        host = host.lower()
    except Exception:
        return "Web"
    if host in SOURCE_LABELS:
        return SOURCE_LABELS[host]
    # Fallback: first label segment of hostname
    clean = host.replace("www.", "").split(".")[0]
    return clean[:1].upper() + clean[1:] if clean else "Web"


def is_target_city(city: str, area: str = "") -> bool:
    """True if the lead is in Bengaluru (city or area mentions Bangalore/Bengaluru)."""
    blob = f"{city or ''} {area or ''}".lower()
    return any(c in blob for c in TARGET_CITIES)


def split_city_area(city_field: str) -> tuple[str, str]:
    """
    'Koramangala, Bengaluru, Karnataka' -> ('Bengaluru', 'Koramangala')
    'Bengaluru'                         -> ('Bengaluru', '')
    'Whitefield, Bangalore'             -> ('Bangalore', 'Whitefield')
    Falls back to the whole string as city.
    """
    if not city_field:
        return "", ""
    parts = [p.strip() for p in city_field.split(",") if p.strip()]
    if not parts:
        return city_field, ""
    # If last part is a state name (Karnataka/Tamil Nadu) and we have >=2, drop it
    state_words = {"karnataka", "tamil nadu", "maharashtra", "delhi", "kerala",
                   "andhra pradesh", "telangana", "uttar pradesh", "west bengal",
                   "gujarat", "rajasthan", "madhya pradesh", "haryana", "punjab",
                   "odisha", "jharkhand", "chhattisgarh", "assam", "uttarakhand",
                   "himachal pradesh", "goa", "tripura", "manipur", "meghalaya",
                   "nagaland", "mizoram", "arunachal pradesh", "sikkim", "bihar"}
    if len(parts) >= 2 and parts[-1].lower() in state_words:
        parts = parts[:-1]
    city = parts[-1]
    area = ", ".join(parts[:-1]) if len(parts) >= 2 else ""
    return city, area


def normalize_date(date_str: Optional[str]) -> Optional[str]:
    """
    Try to parse a wide range of date strings and return ISO 8601.
    Returns None if unparseable.
    """
    if not date_str:
        return None
    s = date_str.strip()
    # Handle 'Today' / 'Yesterday' / 'N days ago'
    low = s.lower()
    now = datetime.utcnow()
    if low in ("today", "just now"):
        return now.isoformat()
    if low == "yesterday":
        return (now - timedelta(days=1)).isoformat()
    m = re.match(r"^(\d+)\s+(day|hour|minute|week|month)s?\s+ago$", low)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {
            "day": timedelta(days=n),
            "hour": timedelta(hours=n),
            "minute": timedelta(minutes=n),
            "week": timedelta(weeks=n),
            "month": timedelta(days=n * 30),
        }[unit]
        return (now - delta).isoformat()
    # Common date formats
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d, %Y",
        "%B %d, %Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
    ):
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    return None


def url_hash(url: str) -> str:
    """Stable hash for dedup when source_url is missing."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def normalize_salary(amount: Optional[str | int | float], currency: str = "INR") -> Optional[str]:
    """
    Coerce '50K - 1.15L', 50000, '50000-115000', etc. into a clean '₹50,000 - ₹1,15,000' string.
    Pass-through already-formatted strings.
    """
    if amount is None or amount == "":
        return None
    if isinstance(amount, str):
        s = amount.strip()
        if not s:
            return None
        if any(sym in s for sym in ["₹", "$", "€", "£"]):
            return s  # already formatted
        # Compact form like '50K - 1.15L' or '50K-1.15L'
        def expand(token: str) -> str:
            t = token.strip().replace(",", "").replace(" ", "")
            mult = 1
            if t.upper().endswith("L"):
                mult, t = 100000, t[:-1]
            elif t.upper().endswith("K"):
                mult, t = 1000, t[:-1]
            elif t.upper().endswith("CR"):
                mult, t = 10000000, t[:-2]
            try:
                return f"₹{int(float(t) * mult):,}"
            except ValueError:
                return token

        if "-" in s or "–" in s or "—" in s:
            sep = next((c for c in ["-", "–", "—"] if c in s), "-")
            parts = [p for p in re.split(r"[-–—]", s) if p.strip()]
            if len(parts) == 2:
                a, b = expand(parts[0]), expand(parts[1])
                if a.startswith("₹") and b.startswith("₹"):
                    return f"{a} - {b}"
        return expand(s)
    if isinstance(amount, (int, float)):
        return f"₹{int(amount):,}"
    return str(amount)


def make_lead(
    *,
    hospital: str,
    role: str,
    source_url: str,
    department: str = "",
    city: str = "",
    area: str = "",
    salary: Optional[str] = None,
    hiring_type: str = "",
    phone: str = "",
    email: str = "",
    contact: str = "",
    notes: str = "",
    date_posted: Optional[str] = None,
    llm_data: Optional[dict] = None,
) -> dict:
    """Build a lead dict matching the Supabase `leads` schema. Always returns the same shape."""
    if not source_url:
        # Skip — no way to dedup
        raise ValueError("source_url is required")
    return {
        "hospital": (hospital or "").strip() or "Unknown Hospital",
        "role": (role or "").strip() or "Medical Staff",
        "department": (department or "").strip() or "General",
        "city": (city or "").strip(),
        "area": (area or "").strip(),
        "salary": (salary or "").strip() or None,
        "hiring_type": (hiring_type or "").strip(),
        "phone": (phone or "").strip(),
        "email": (email or "").strip(),
        "contact": (contact or "").strip(),
        "notes": (notes or "").strip(),
        "date_posted": normalize_date(date_posted) if date_posted else None,
        "source_url": source_url,
        "source": derive_source_label(source_url),
        "llm_data": llm_data if llm_data else None,
    }


class HttpClient:
    """Thin wrapper over httpx with a desktop browser User-Agent and per-host cooldown."""

    DEFAULT_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(self, timeout: float = 30.0, user_agent: str = DEFAULT_UA):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept-Language": "en-IN,en;q=0.9"},
            follow_redirects=True,
        )
        self._last_request_per_host: dict[str, float] = {}

    async def get(self, url: str, **kwargs) -> httpx.Response:
        import asyncio
        host = urlparse(url).hostname or ""
        last = self._last_request_per_host.get(host, 0)
        elapsed = asyncio.get_event_loop().time() - last
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        resp = await self._client.get(url, **kwargs)
        self._last_request_per_host[host] = asyncio.get_event_loop().time()
        return resp

    async def post(self, url: str, **kwargs) -> httpx.Response:
        import asyncio
        host = urlparse(url).hostname or ""
        last = self._last_request_per_host.get(host, 0)
        elapsed = asyncio.get_event_loop().time() - last
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        resp = await self._client.post(url, **kwargs)
        self._last_request_per_host[host] = asyncio.get_event_loop().time()
        return resp

    async def aclose(self):
        await self._client.aclose()
