"""Turn the text values scraped from ufcstats.com into typed values.

Shared by the seed script and, later, by the agent's submit tools.
"""
import datetime
import re
import unicodedata
from urllib.parse import urlparse

MISSING = {"", "-", "--", "---", "n/a", "none", "null"}


def clean(value):
    if value is None:
        return None
    text = " ".join(str(value).split())
    return None if text.lower() in MISSING else text


def to_int(value):
    text = clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group()) if match else None


def to_float(value):
    text = clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def percent_to_fraction(value):
    """'45%' -> 0.45"""
    number = to_float(value)
    return None if number is None else round(number / 100, 4)


def height_to_inches(value):
    """5' 11" -> 71.0"""
    text = clean(value)
    if text is None:
        return None
    match = re.match(r"(\d+)\s*'\s*(\d+(?:\.\d+)?)?", text)
    if not match:
        return None
    feet = int(match.group(1))
    inches = float(match.group(2) or 0)
    return feet * 12 + inches


def parse_date(value):
    """'Jul 22, 1989' or 'October 11, 2025' -> date"""
    text = clean(value)
    if text is None:
        return None
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def fight_time(value):
    """'4:32' -> '4:32'; anything else -> None"""
    text = clean(value)
    return text if text and re.fullmatch(r"\d{1,2}:\d{2}", text) else None


def fight_round(value):
    number = to_int(value)
    return number if number is not None and 1 <= number <= 5 else None


def ufcstats_id(url):
    """'http://ufcstats.com/fighter-details/abc123' -> 'abc123'"""
    text = clean(url)
    if text is None:
        return None
    segment = urlparse(text).path.rstrip("/").rsplit("/", 1)[-1]
    return segment or None


# Letters that NFKD does not split into base letter + accent.
TRANSLITERATE = str.maketrans({
    "ł": "l", "Ł": "L", "ø": "o", "Ø": "O", "đ": "d", "Đ": "D",
    "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ı": "i",
})


def name_key(name):
    """Lowercase, accent-free, punctuation-free key used to match fighter names across sources."""
    text = unicodedata.normalize("NFKD", (name or "").translate(TRANSLITERATE))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def inches_text(value):
    """71.0 -> '71', 71.5 -> '71.5' (the backend stores inches as text)."""
    if value is None:
        return None
    return str(int(value)) if float(value).is_integer() else str(value)


def split_location(location):
    """'Las Vegas, Nevada, USA' -> ('Las Vegas', 'USA')"""
    parts = [part.strip() for part in (location or "").split(",") if part.strip()]
    city = parts[0] if parts else None
    country = parts[-1] if len(parts) > 1 else None
    return city, country
