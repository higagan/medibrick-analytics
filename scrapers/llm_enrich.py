"""
LLM enrichment for scraped leads via local Ollama (default medgemma:4b).

Uses Google's medical-tuned Gemma 3 (3.3GB) to classify role, extract
department, normalize hospital name, and produce a 1-line sales-rep
summary. Result is stored in `llm_data` (a JSONB column in Supabase)
and never overwrites the raw scraper output. If Ollama is offline or
the model isn't loaded, we silently skip enrichment.
"""
from __future__ import annotations
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
# medgemma:4b is Google DeepMind's medical-tuned Gemma 3 (3.3GB). It
# was trained on medical text, QA pairs, FHIR records, radiology and
# dermatology text - perfect for our job-classification use case.
# Override with OLLAMA_MODEL env var.
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "medgemma:4b")
ENABLE_LLM = os.getenv("ENABLE_LLM", "1") == "1"
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "30"))

ROLE_CHOICES = ["doctor", "rmo", "bams", "bhms", "specialist", "locum", "nurse"]
DEPARTMENT_CHOICES = [
    "General Medicine", "Cardiology", "ICU / Critical Care", "ER / Emergency",
    "Pediatrics", "Obstetrics & Gynecology", "Orthopedics", "Neurology",
    "Oncology", "Dermatology", "Ophthalmology", "ENT", "Psychiatry",
    "Anesthesiology", "Radiology", "Pathology", "Surgery", "Ayurveda",
    "Homeopathy", "Pharmacy", "Administration", "Other",
]

_SYSTEM_PROMPT = (
    "You are a medical-recruitment classifier for MediBrick, a Bangalore-based "
    "healthcare staffing company. You classify Indian job-board postings into "
    "structured fields. Reply ONLY with strict JSON - no prose, no markdown "
    "fences. If a field is unclear, return null for it. Never invent data."
)

_USER_TEMPLATE = """Classify this job posting and return strict JSON:

  role: one of {role_choices}
  department: one of {dept_choices} (or null)
  hospital_canonical: official name (strip Pvt Ltd/Limited/LLP, title-case, collapse variants)
  summary: ONE sentence (<=140 chars) for a sales rep
  hiring_type: "Full-time"|"Part-time"|"Contract"|"Locum"|"Permanent"|"Temporary"|"Walk-in"|null
  quality_score: float 0-1 (real + recent + relevant)
  is_relevant: bool (clinical medical role in/near Bengaluru)

hospital (raw): {hospital}
role (raw): {role}
city: {city}
area: {area}
salary: {salary}
date_posted: {date_posted}
source: {source}
source_url: {source_url}
"""


def _build_prompt(lead):
    return _USER_TEMPLATE.format(
        role_choices=", ".join(ROLE_CHOICES),
        dept_choices=", ".join(DEPARTMENT_CHOICES),
        hospital=lead.get("hospital") or "(missing)",
        role=lead.get("role") or "(missing)",
        city=lead.get("city") or "",
        area=lead.get("area") or "",
        salary=lead.get("salary") or "",
        date_posted=lead.get("date_posted") or "",
        source=lead.get("source") or "",
        source_url=lead.get("source_url") or "",
    )


def _strip_code_fence(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n", 1)
        text = lines[1] if len(lines) > 1 else ""
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()


def _parse_response(text):
    text = _strip_code_fence(text)
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _validate(data):
    """Sanitize the LLM output: drop unknown enum values, coerce types."""
    out = {}
    role = data.get("role")
    if role in ROLE_CHOICES:
        out["role"] = role
    dept = data.get("department")
    if dept in DEPARTMENT_CHOICES:
        out["department"] = dept
    hc = data.get("hospital_canonical")
    if isinstance(hc, str) and hc.strip():
        out["hospital_canonical"] = hc.strip()[:120]
    summary = data.get("summary")
    if isinstance(summary, str):
        out["summary"] = summary.strip()[:200]
    ht = data.get("hiring_type")
    if isinstance(ht, str) and ht.strip():
        out["hiring_type"] = ht.strip()[:50]
    qs = data.get("quality_score")
    if isinstance(qs, (int, float)):
        out["quality_score"] = max(0.0, min(1.0, float(qs)))
    rel = data.get("is_relevant")
    if isinstance(rel, bool):
        out["is_relevant"] = rel
    return out


def _call_ollama(prompt):
    """Call Ollama /api/chat. Returns parsed dict or None on any failure."""
    try:
        with httpx.Client(timeout=LLM_TIMEOUT_S) as client:
            resp = client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": OLLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "stream": False,
                    "options": {"num_predict": 250, "temperature": 0.0},
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as e:
        logger.debug(f"Ollama call failed: {e}")
        return None
    text = (payload.get("message") or {}).get("content") or payload.get("response")
    if not text:
        return None
    return _parse_response(text)


def is_available():
    """Check if Ollama is reachable. Use at startup to log a clear warning."""
    if not ENABLE_LLM:
        return False
    try:
        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{OLLAMA_URL}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def enrich_lead(lead):
    """
    Return a NEW lead dict with `llm_data` populated. Does NOT mutate input.
    Skips silently if Ollama is offline, the model is missing, or the lead
    has no role text. Latency: ~1-3s per lead on M-series Mac with medgemma:4b.
    """
    if not ENABLE_LLM:
        return lead
    if not lead.get("role") and not lead.get("hospital"):
        return lead
    prompt = _build_prompt(lead)
    raw = _call_ollama(prompt)
    if raw is None:
        return lead
    validated = _validate(raw)
    if not validated:
        return lead
    out = dict(lead)
    out["llm_data"] = validated
    return out


def enrich_batch(leads, concurrency=1):
    """
    Enrich a list of leads sequentially. Ollama is local so concurrency=1
    is usually fastest. Returns the same list with `llm_data` populated
    where successful.
    """
    if not leads:
        return leads
    if not is_available():
        logger.warning(
            f"Ollama not reachable at {OLLAMA_URL} - skipping LLM enrichment "
            f"for {len(leads)} leads. Start Ollama and pull the model: "
            f"`ollama pull {OLLAMA_MODEL}`"
        )
        return leads
    logger.info(f"LLM enriching {len(leads)} leads via {OLLAMA_MODEL} at {OLLAMA_URL}...")
    t0 = time.time()
    enriched = []
    n_ok = 0
    for lead in leads:
        e = enrich_lead(lead)
        if e.get("llm_data"):
            n_ok += 1
        enriched.append(e)
    logger.info(
        f"LLM enrichment done in {time.time()-t0:.1f}s - "
        f"{n_ok}/{len(leads)} succeeded"
    )
    return enriched
