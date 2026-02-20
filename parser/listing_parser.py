"""
Parse raw event text scraped from Sheroot into structured Listing objects.
"""

import re
import hashlib
from dataclasses import dataclass, field
from datetime import date as Date, datetime


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Listing:
    title: str
    raw_text: str
    date: str                        # ISO date string from calendar event
    price: float | None = None       # Parsed reserve / asking price in ZAR
    location: str = ""               # Best-guess suburb/town
    erf_number: str = ""             # ERF XXXX
    property_type: str = ""          # house, flat, sectional title, plot, …
    description: str = ""            # cleaned full text

    def listing_hash(self) -> str:
        """Stable hash for deduplication – based on title + date + price."""
        key = f"{self.title}|{self.date}|{self.price}"
        return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_PRICE_RE = re.compile(
    r"R\s*[\d\s,]+(?:\.\d{2})?",
    re.IGNORECASE,
)

_ERF_RE = re.compile(
    r"\bERF\s*(\d+)\b",
    re.IGNORECASE,
)

_DATE_RE = re.compile(
    r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})\b"
    r"|\b(\d{4})[/-](\d{1,2})[/-](\d{1,2})\b",
)

_PROPERTY_TYPE_KEYWORDS: list[tuple[str, str]] = [
    (r"\bsectional\s+title\b", "sectional title"),
    (r"\bapartment\b", "apartment"),
    (r"\bflat\b", "flat"),
    (r"\bplot\b", "plot"),
    (r"\bstand\b", "stand"),
    (r"\bhouse\b", "house"),
    (r"\bdwelling\b", "house"),
    (r"\bproperty\b", "property"),
    (r"\bunit\b", "unit"),
    (r"\bvacant\s+land\b", "vacant land"),
    (r"\bfarm\b", "farm"),
]

# Suburb / town keywords commonly appearing in South African property listings
_KNOWN_SUBURBS: list[str] = [
    "Roodepoort", "Krugersdorp", "Johannesburg", "Pretoria", "Soweto",
    "Randburg", "Sandton", "Centurion", "Midrand", "Kempton Park",
    "Boksburg", "Benoni", "Springs", "Germiston", "Alberton",
    "Vereeniging", "Vanderbijlpark", "Sasolburg", "Potchefstroom",
    "Klerksdorp", "Rustenburg", "Polokwane", "Nelspruit", "Mbombela",
    "Witbank", "Emalahleni", "Middelburg", "Secunda",
    "Cape Town", "Bellville", "Durban", "Pinetown", "Umhlanga",
    "Port Elizabeth", "Gqeberha", "East London", "Bloemfontein",
]


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_price(text: str) -> float | None:
    """Return the largest Rand amount found (likely the reserve/asking price)."""
    matches = _PRICE_RE.findall(text)
    if not matches:
        return None
    amounts: list[float] = []
    for m in matches:
        digits = re.sub(r"[^\d.]", "", m)
        try:
            amounts.append(float(digits))
        except ValueError:
            pass
    return max(amounts) if amounts else None


def _parse_erf(text: str) -> str:
    m = _ERF_RE.search(text)
    return f"ERF {m.group(1)}" if m else ""


def _parse_property_type(text: str) -> str:
    lower = text.lower()
    for pattern, label in _PROPERTY_TYPE_KEYWORDS:
        if re.search(pattern, lower):
            return label
    return ""


def _parse_location(text: str) -> str:
    """Return the first recognised suburb/town found in the text."""
    lower = text.lower()
    for suburb in _KNOWN_SUBURBS:
        if suburb.lower() in lower:
            return suburb
    # Fallback: look for a capitalised word following common address indicators
    m = re.search(
        r"(?:situated\s+at|located\s+at|in|at)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        text,
    )
    if m:
        return m.group(1)
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_listing(raw_event: dict) -> Listing:
    """
    Convert a raw event dict (from the scraper) into a structured Listing.

    raw_event keys: title, date, description, raw_text, location (optional)
    The scraper now returns a 'location' field directly from the calendar API.
    """
    title = raw_event.get("title", "")
    date_str = raw_event.get("date", "")
    raw_text = raw_event.get("raw_text", "") or raw_event.get("description", "")

    # Prefer the structured location from the API; fall back to text parsing
    api_location = raw_event.get("location", "")

    listing = Listing(
        title=title,
        raw_text=raw_text,
        date=date_str,
        price=_parse_price(raw_text),
        location=api_location or _parse_location(raw_text),
        erf_number=_parse_erf(raw_text),
        property_type=_parse_property_type(raw_text),
        description=raw_text.strip(),
    )
    return listing


def parse_listings(raw_events: list[dict]) -> list[Listing]:
    return [parse_listing(e) for e in raw_events]


# ---------------------------------------------------------------------------
# Per-property parsing
# ---------------------------------------------------------------------------

_NO_COURT_RESERVE_RE = re.compile(r'\bNo\s+Court\s+Reserve\b', re.IGNORECASE)
_BANK_RESERVE_RE = re.compile(r'\bBank\s+Reserve\b', re.IGNORECASE)
_COURT_RESERVE_RE = re.compile(
    r'R\s*([\d][\d\s,]*)(?:\.\d+)?\s*(?:\u2013\s*)?Court\s+Reserve',
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r'(\d[\d\s]*)m\u00b2', re.IGNORECASE)

# Split on numbered property entries like "1. " or "12. " at start of line
_PROPERTY_SPLIT_RE = re.compile(r'^(\d{1,2})\.\s', re.MULTILINE)

# Headers/footers that mark the start and end of the properties section
_SECTION_START_RE = re.compile(r'NO IMAGE\s+ADDRESS', re.IGNORECASE)
_SECTION_END_RE = re.compile(
    r'(?:The properties listed above|RULES OF SALES? IN EXECUTION)',
    re.IGNORECASE,
)


@dataclass
class Property:
    sale_date: str          # ISO auction date (YYYY-MM-DD)
    number: int             # property number in PDF
    raw_text: str           # full block text (address + description interleaved)
    size_m2: float | None
    reserve_price: float | None   # None for Bank / NCR
    reserve_type: str       # 'court' | 'bank' | 'none' | 'unknown'
    pdf_url: str

    def property_hash(self) -> str:
        key = f"{self.sale_date}|{self.number}|{self.raw_text[:80]}"
        return hashlib.sha256(key.encode()).hexdigest()

    @property
    def is_opportunity(self) -> bool:
        return self.reserve_type in ('none', 'bank')

    @property
    def reserve_display(self) -> str:
        if self.reserve_type == 'none':
            return "\u26a1 *NO COURT RESERVE \u2014 any bid wins*"
        if self.reserve_type == 'bank':
            return "\U0001f3e6 *Bank Reserve* (floor set by bank)"
        if self.reserve_type == 'court' and self.reserve_price is not None:
            return f"\U0001f4b0 Court Reserve: R {self.reserve_price:,.0f}"
        return "\u2753 Reserve unknown"


def _parse_size(text: str) -> float | None:
    m = _SIZE_RE.search(text)
    if not m:
        return None
    raw = re.sub(r'\s', '', m.group(1))
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_reserve(text: str) -> tuple[float | None, str]:
    """Return (reserve_price, reserve_type)."""
    if _NO_COURT_RESERVE_RE.search(text):
        return None, 'none'
    if _BANK_RESERVE_RE.search(text):
        return None, 'bank'
    m = _COURT_RESERVE_RE.search(text)
    if m:
        raw = re.sub(r'[\s,]', '', m.group(1))
        try:
            return float(raw), 'court'
        except ValueError:
            return None, 'court'
    return None, 'unknown'


def property_from_db(row: dict) -> "Property":
    """Reconstruct a Property dataclass from a DB row dict."""
    return Property(
        sale_date=row["sale_date"],
        number=row["property_number"],
        raw_text=row.get("raw_text") or "",
        size_m2=row.get("size_m2"),
        reserve_price=row.get("reserve_price"),
        reserve_type=row.get("reserve_type") or "unknown",
        pdf_url=row.get("pdf_url") or "",
    )


def parse_pdf_properties(pdf_text: str, sale_date: str, pdf_url: str) -> list[Property]:
    """
    Parse individual property entries from extracted PDF text.

    Returns a list of Property objects, one per numbered entry.
    """
    if not pdf_text:
        return []

    # Trim to the properties section
    start_m = _SECTION_START_RE.search(pdf_text)
    body = pdf_text[start_m.end():] if start_m else pdf_text

    end_m = _SECTION_END_RE.search(body)
    if end_m:
        body = body[:end_m.start()]

    # Split on numbered headings
    parts = _PROPERTY_SPLIT_RE.split(body)
    # parts pattern after split: [pre, num, block, num, block, ...]
    # parts[0] is text before first number; skip it
    properties: list[Property] = []
    i = 1
    while i + 1 < len(parts):
        num_str = parts[i]
        block = parts[i + 1].strip()
        i += 2

        if len(block) < 20:
            continue

        try:
            number = int(num_str)
        except ValueError:
            continue

        size = _parse_size(block)
        reserve_price, reserve_type = _parse_reserve(block)

        properties.append(Property(
            sale_date=sale_date,
            number=number,
            raw_text=block,
            size_m2=size,
            reserve_price=reserve_price,
            reserve_type=reserve_type,
            pdf_url=pdf_url,
        ))

    return properties
