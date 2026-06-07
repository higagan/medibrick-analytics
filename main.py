import os
import asyncio
import secrets
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
from typing import List, Optional

from db import get_cached_results, merge_and_save_results

# Load env vars
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = FastAPI(title="Bangalore Medical Finder")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GOOGLE_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
# Simple shared-secret auth for internal team. Set in env or auto-generate per session.
APP_PASSWORD = os.getenv("APP_PASSWORD")

# In-memory session tokens (token -> created_at). For internal use only.
import time
_sessions: dict = {}


def create_session_token() -> str:
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time()
    return token


def verify_session(token: Optional[str]) -> bool:
    if not token or token not in _sessions:
        return False
    # Expire after 7 days
    if time.time() - _sessions[token] > 7 * 24 * 3600:
        del _sessions[token]
        return False
    return True


def require_auth(authorization: Optional[str] = Header(None)) -> None:
    """Dependency: require valid session token in Authorization header."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:].strip()
    if not verify_session(token):
        raise HTTPException(status_code=401, detail="Invalid or expired session")


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str

BANGALORE_AREAS = [
    "HSR Layout",
    "Sarjapur Road",
    "Koramangala",
    "Indiranagar",
    "Whitefield",
    "Electronic City",
    "Jayanagar",
    "Marathahalli",
    "MG Road",
    "Malleshwaram",
]

MEDICAL_AMENITIES = ["hospital", "clinic", "doctors", "pharmacy", "dentist"]

# Google Places type mapping
GOOGLE_TYPE_MAP = {
    "hospital": "hospital",
    "clinic": "doctor",
    "doctors": "doctor",
    "pharmacy": "pharmacy",
    "dentist": "dentist",
}

# Reverse mapping for display
REVERSE_TYPE_MAP = {
    "hospital": "hospital",
    "doctor": "clinic",
    "pharmacy": "pharmacy",
    "dentist": "dentist",
    "health": "hospital",
}


class SearchRequest(BaseModel):
    area: str
    filter: Optional[str] = None


class MedicalCenter(BaseModel):
    name: str
    type: str
    address: Optional[str] = None
    lat: float
    lon: float
    phone: Optional[str] = None
    opening_hours: Optional[str] = None
    osm_url: str


class SearchResponse(BaseModel):
    results: List[MedicalCenter]
    counts: Optional[dict] = None
    message: Optional[str] = None


@app.post("/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    """Simple password login. Returns a session token."""
    if not APP_PASSWORD:
        raise HTTPException(status_code=500, detail="APP_PASSWORD not configured on server")
    if not secrets.compare_digest(req.password, APP_PASSWORD):
        raise HTTPException(status_code=401, detail="Wrong password")
    token = create_session_token()
    return LoginResponse(token=token)


@app.get("/areas", dependencies=[])
def get_areas():
    # Areas list is public so the login page can render the UI shell.
    return {"areas": BANGALORE_AREAS}


async def google_geocode(area: str) -> tuple[float, float]:
    """Geocode area using Google Geocoding API."""
    url = "https://maps.googleapis.com/maps/api/geocode/json"
    params = {
        "address": f"{area}, Bangalore, India",
        "key": GOOGLE_API_KEY,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK" or not data.get("results"):
            raise ValueError(f"Geocoding failed: {data.get('status')}")
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]


async def google_nearby_search(lat: float, lon: float, amenity: str) -> List[dict]:
    """Search nearby medical facilities using Google Places API."""
    google_type = GOOGLE_TYPE_MAP.get(amenity, "hospital")
    url = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
    params = {
        "location": f"{lat},{lon}",
        "radius": 2500,
        "type": google_type,
        "key": GOOGLE_API_KEY,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "OK":
            raise ValueError(f"Places API error: {data.get('status')}")
        return data.get("results", [])


async def fetch_from_google(area: str) -> List[MedicalCenter]:
    """Primary data source: Google Places API."""
    lat, lon = await google_geocode(area)

    all_results: List[MedicalCenter] = []
    seen_names = set()

    for amenity in MEDICAL_AMENITIES:
        try:
            places = await google_nearby_search(lat, lon, amenity)
            for place in places:
                name = place.get("name", "Unnamed")
                if name in seen_names:
                    continue
                seen_names.add(name)

                loc = place.get("geometry", {}).get("location", {})
                plat = loc.get("lat", 0.0)
                plon = loc.get("lng", 0.0)

                # Map Google type back to our type
                types = place.get("types", [])
                gtype = types[0] if types else "hospital"
                mapped_type = REVERSE_TYPE_MAP.get(gtype, "hospital")

                all_results.append(
                    MedicalCenter(
                        name=name,
                        type=mapped_type,
                        address=place.get("vicinity"),
                        lat=plat,
                        lon=plon,
                        phone=None,
                        opening_hours=None,
                        osm_url=f"https://www.google.com/search?q={name.replace(' ', '+')}+Bangalore",
                    )
                )
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"Google Places error for {amenity}: {e}")
            continue

    return all_results


async def fetch_from_osm(area: str) -> List[MedicalCenter]:
    """Fallback data source: OpenStreetMap / Overpass."""
    nominatim_url = "https://nominatim.openstreetmap.org/search"
    query = f"{area}, Bangalore, India"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            resp = await client.get(
                nominatim_url,
                params={"q": query, "format": "json", "limit": 1, "addressdetails": 1},
                headers={"User-Agent": "BangaloreMedicalFinder/1.0"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            raise ValueError(f"Geocoding failed: {e}")

    if not data:
        raise ValueError(f"Could not locate '{area}'")

    place = data[0]
    bbox = place.get("boundingbox")
    lat = float(place.get("lat", 0))
    lon = float(place.get("lon", 0))

    if bbox and len(bbox) == 4:
        south, north, west, east = map(float, bbox)
        if (north - south) < 0.01 or (east - west) < 0.01:
            delta = 0.022
            south, north = lat - delta, lat + delta
            west, east = lon - delta, lon + delta
    else:
        delta = 0.022
        south, north = lat - delta, lat + delta
        west, east = lon - delta, lon + delta

    await asyncio.sleep(1)

    overpass_url = "https://overpass-api.de/api/interpreter"
    bbox_str = f"({south},{west},{north},{east})"
    union_parts = "\n".join(
        f'      node["amenity"="{a}"]{bbox_str};\n      way["amenity"="{a}"]{bbox_str};\n      relation["amenity"="{a}"]{bbox_str};'
        for a in MEDICAL_AMENITIES
    )
    overpass_query = f"""
[out:json][timeout:25];
(
{union_parts}
);
out body center;
"""

    overpass_data = None
    for attempt in range(3):
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.post(
                    overpass_url,
                    data={"data": overpass_query},
                    headers={"User-Agent": "BangaloreMedicalFinder/1.0"},
                )
                resp.raise_for_status()
                overpass_data = resp.json()
                break
            except Exception:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)

    elements = overpass_data.get("elements", [])
    if not elements:
        raise ValueError("No medical centers found in OpenStreetMap")

    results: List[MedicalCenter] = []
    seen_ids = set()
    for el in elements:
        uid = f"{el.get('type')}:{el.get('id')}"
        if uid in seen_ids:
            continue
        seen_ids.add(uid)

        tags = el.get("tags", {})
        name = tags.get("name", "Unnamed")
        amenity = tags.get("amenity", "unknown")

        if "lat" in el and "lon" in el:
            elat, elon = el.get("lat", 0.0), el.get("lon", 0.0)
        else:
            center = el.get("center", {})
            elat, elon = center.get("lat", 0.0), center.get("lon", 0.0)

        addr_parts = []
        for key in ["addr:housenumber", "addr:street", "addr:suburb", "addr:city", "addr:postcode"]:
            val = tags.get(key)
            if val:
                addr_parts.append(val)
        address = ", ".join(addr_parts) if addr_parts else None

        results.append(
            MedicalCenter(
                name=name,
                type=amenity,
                address=address,
                lat=elat,
                lon=elon,
                phone=tags.get("phone") or tags.get("contact:phone"),
                opening_hours=tags.get("opening_hours"),
                osm_url=f"https://www.openstreetmap.org/?mlat={elat}&mlon={elon}#map=18/{elat}/{elon}",
            )
        )

    return results


@app.post("/search", response_model=SearchResponse, dependencies=[])
async def search_medical(request: SearchRequest, authorization: Optional[str] = Header(None)):
    area = request.area.strip()
    if not area:
        return SearchResponse(results=[], message="Area is required.")

    # 0. Check Supabase cache first
    try:
        cached = await get_cached_results(area)
        if cached is not None:
            results = [MedicalCenter(**item) for item in cached]
            if request.filter:
                allowed = {a.strip().lower() for a in request.filter.split(",") if a.strip()}
                results = [r for r in results if r.type in allowed]
            counts = {}
            for r in results:
                counts[r.type] = counts.get(r.type, 0) + 1
            return SearchResponse(results=results, counts=counts)
    except Exception as e:
        print(f"Cache read error: {e}")

    # Require auth for cache-miss (fresh fetches)
    require_auth(authorization)

    # 1. Try Google Places (primary)
    results: List[MedicalCenter] = []
    source = "google"
    try:
        if GOOGLE_API_KEY:
            results = await fetch_from_google(area)
        else:
            raise ValueError("No Google API key configured")
    except Exception as e:
        print(f"Google Places failed, falling back to OSM: {e}")
        source = "osm"
        try:
            results = await fetch_from_osm(area)
        except Exception as e2:
            return SearchResponse(results=[], message=f"Search failed: {str(e2)}")

    # 2. Merge and save to Supabase cache (keeps existing + adds new)
    try:
        await merge_and_save_results(area, [r.model_dump() for r in results])
    except Exception as e:
        print(f"Cache write error: {e}")

    # 3. Apply frontend filter
    if request.filter:
        allowed = {a.strip().lower() for a in request.filter.split(",") if a.strip()}
        results = [r for r in results if r.type in allowed]

    # 4. Compute type counts
    counts = {}
    for r in results:
        counts[r.type] = counts.get(r.type, 0) + 1

    return SearchResponse(results=results, counts=counts, message=f"Source: {source}")


app.mount("/", StaticFiles(directory="static", html=True), name="static")
