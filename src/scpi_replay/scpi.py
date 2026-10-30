"""Pure string helpers for SCPI text: query detection, normalisation, fields."""

from __future__ import annotations

import math
import re

_NODE = re.compile(r"([A-Za-z]+)(\d*)(\?)?")
_NUMBER = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_VOWELS = "AEIOU"


def _split_outside_quotes(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside "..." / '...' strings (doubled quotes escape)."""
    parts: list[str] = []
    quote = ""
    start = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == sep:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _header(unit: str) -> str:
    parts = unit.strip().split(None, 1)
    return parts[0] if parts else ""


def is_query(text: str) -> bool:
    """True if any ;-separated unit has a header ending in ``?``."""
    return any(_header(u).endswith("?") for u in _split_outside_quotes(text, ";"))


def _short_node(node: str) -> str:
    m = _NODE.fullmatch(node)
    if not m:
        return node.upper()
    word, digits, q = m.group(1).upper(), m.group(2), m.group(3) or ""
    if len(word) > 4:
        word = word[:3] if word[3] in _VOWELS else word[:4]
    return word + digits + q


def _normalize_unit(unit: str) -> str:
    parts = unit.strip().split(None, 1)
    if not parts:
        return ""
    header = parts[0]
    if header.startswith("*"):
        header = header.upper()
    else:
        header = ":".join(_short_node(n) for n in header.split(":"))
    return header + (" " + parts[1].strip() if len(parts) > 1 else "")


def normalize(text: str) -> str:
    """Upper-case headers in short form; arguments are left untouched."""
    return ";".join(_normalize_unit(u) for u in _split_outside_quotes(text, ";"))


def split_fields(text: str) -> list[str]:
    """Split a response on commas outside quoted strings."""
    return _split_outside_quotes(text, ",")


def parse_number(field: str) -> float | None:
    """Parse a SCPI numeral; None for non-numeric or non-finite values."""
    s = field.strip()
    if not _NUMBER.fullmatch(s):
        return None
    value = float(s)
    return value if math.isfinite(value) else None
