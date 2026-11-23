"""Conservative phrase and explicit radio-address extraction."""
from __future__ import annotations

import re
import unicodedata

CALLSIGN = (
    r"(?:[A-Z][A-Z-]{1,14}\s+\d{1,4}[A-Z]?|"
    r"[A-Z]{1,5}-?\d{1,4}[A-Z]?|"
    r"DISPATCH|BASE|CONTROL|COMMAND|OPERATIONS|TOWER|GROUND|APPROACH)"
)
_TO_PATTERN = re.compile(
    rf"\b(?P<source>{CALLSIGN})\s+to\s+(?P<target>{CALLSIGN})\b",
    re.IGNORECASE,
)
_THIS_IS_PATTERN = re.compile(
    rf"\b(?P<target>{CALLSIGN})\s*[,;:]?\s+this is\s+(?P<source>{CALLSIGN})\b",
    re.IGNORECASE,
)
_FROM_PATTERN = re.compile(
    rf"\b(?P<target>{CALLSIGN})\s*[,;:]?\s+from\s+(?P<source>{CALLSIGN})\b",
    re.IGNORECASE,
)

_MESSAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("readback_or_acknowledgement", re.compile(
        r"\b(?:roger|copy|wilco|affirmative|negative|10[- ]?4|received)\b", re.I)),
    ("repeat_or_correction", re.compile(
        r"\b(?:say again|repeat|correction|scratch that|disregard)\b", re.I)),
    ("hold_or_wait", re.compile(r"\b(?:stand by|wait one|hold traffic)\b", re.I)),
    ("status_update", re.compile(
        r"\b(?:en\s+route|on scene|arrived|in service|out of service|"
        r"available|unavailable|transporting|clear(?: of| from)? scene)\b", re.I)),
    ("handoff_or_channel_change", re.compile(
        r"\b(?:switch(?:ing)? (?:to )?(?:channel|frequency)|"
        r"change (?:to )?(?:channel|frequency)|hand(?:ing)? off)\b", re.I)),
    ("closing", re.compile(r"\b(?:over|out|standing by)\b", re.I)),
)


def normalize_identifier(value: str) -> str:
    """Normalize presentation only; do not infer that similar IDs are aliases."""
    return " ".join(unicodedata.normalize("NFC", value).upper().split())


def classify_message(text: str) -> str:
    for kind, pattern in _MESSAGE_PATTERNS:
        if pattern.search(text):
            return kind
    return "other_or_unclassified"


def radio_contact(text: str) -> tuple[str, str, str] | None:
    """Return explicit (origin, addressee, pattern), otherwise no edge."""
    for name, pattern in (
        ("origin_to_addressee", _TO_PATTERN),
        ("addressee_this_is_origin", _THIS_IS_PATTERN),
        ("addressee_from_origin", _FROM_PATTERN),
    ):
        match = pattern.search(text)
        if match:
            origin = normalize_identifier(match.group("source"))
            addressee = normalize_identifier(match.group("target"))
            if origin != addressee:
                return origin, addressee, name
    return None
