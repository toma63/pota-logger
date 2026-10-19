"""Input validation shared by the prompts and the CLI."""

from __future__ import annotations

import re

# POTA references are a 1-4 character program prefix and a 4-5 digit number,
# e.g. US-0001, CA-0123, GB-0033, 9A-0001.
PARK_RE = re.compile(r"^[0-9A-Z]{1,4}-[0-9]{3,5}$")

# Callsigns, allowing portable prefixes and suffixes such as VE3/W1AW/P.
CALL_RE = re.compile(r"^[0-9A-Z/]{3,16}$")

# Maidenhead locator: field, square, and optional subsquare/extended square.
GRID_RE = re.compile(r"^[A-R]{2}[0-9]{2}([A-X]{2}([0-9]{2})?)?$", re.IGNORECASE)


def normalize_call(value: str) -> str:
    """Upper-case and strip a callsign as typed."""
    return value.strip().upper().replace("\\", "/")


def is_valid_call(value: str) -> bool:
    call = normalize_call(value)
    return bool(CALL_RE.match(call)) and any(c.isdigit() for c in call)


def normalize_park(value: str) -> str:
    """Upper-case a park reference and tolerate a missing hyphen (``US0001``)."""
    park = value.strip().upper().replace(" ", "")
    if "-" not in park:
        match = re.match(r"^([0-9A-Z]{1,4}?)([0-9]{4,5})$", park)
        if match:
            park = f"{match.group(1)}-{match.group(2)}"
    return park


def is_valid_park(value: str) -> bool:
    return bool(PARK_RE.match(normalize_park(value)))


def normalize_grid(value: str) -> str:
    """Normalize a Maidenhead locator to the conventional AB12cd casing."""
    grid = value.strip().replace(" ", "")
    if not grid:
        return ""
    head, sub, ext = grid[:4].upper(), grid[4:6].lower(), grid[6:8]
    return f"{head}{sub}{ext}"


def is_valid_grid(value: str) -> bool:
    return bool(GRID_RE.match(value.strip())) if value.strip() else False


def base_call(value: str) -> str:
    """Strip portable prefixes/suffixes to the callsign a database can look up.

    ``VE3/W1AW/P`` and ``W1AW/4`` both resolve to ``W1AW``: the segment holding
    both letters and a digit, preferring the longest when several qualify.
    """
    call = normalize_call(value)
    parts = [p for p in call.split("/") if p]
    if len(parts) <= 1:
        return call
    candidates = [
        p for p in parts
        if any(c.isdigit() for c in p) and any(c.isalpha() for c in p) and len(p) >= 3
    ]
    if not candidates:
        return max(parts, key=len)
    return max(candidates, key=len)
