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
    ("emergency_traffic", re.compile(r"\b(?:emergency traffic|urgent traffic)\b", re.I)),
    ("readback_or_acknowledgement", re.compile(
        r"\b(?:roger|cop(?:y|ies)|wilco|affirmative|negative|10[- ]?4|received)\b", re.I)),
    ("repeat_or_correction", re.compile(
        r"\b(?:say again|repeat|correction|scratch that|disregard)\b", re.I)),
    ("hold_or_wait", re.compile(r"\b(?:stand by|wait one|hold traffic)\b", re.I)),
    ("status_update", re.compile(
        r"\b(?:en[- ]?route|responding|on scene|at scene|arrived|in service|"
        r"out of service|available|unavailable|transporting|"
        r"clear(?:ed)?(?: of| from)? scene|returning(?: to station)?|back at station|"
        r"all clear|under control)\b", re.I)),
    ("handoff_or_channel_change", re.compile(
        r"\b(?:switch(?:ing)? (?:to )?(?:channel|frequency)|"
        r"change (?:to )?(?:channel|frequency)|hand(?:ing)? off)\b", re.I)),
    ("closing", re.compile(r"\b(?:over|out|standing by)\b", re.I)),
)

_CALL_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("fire", re.compile(
        r"\b(?:fire|firefighter|fire department|structure fire|brush fire|"
        r"box alarm|hydrant|smoke|engine\s+\d+|ladder\s+\d+|battalion\s+\d+)\b", re.I)),
    ("police", re.compile(
        r"\b(?:police|officer|patrol|sheriff|suspect|traffic stop|arrest|warrant|"
        r"burglary|robbery|law enforcement|domestic dispute)\b", re.I)),
    ("ems", re.compile(
        r"\b(?:ems|ambulance|medic\s+\d+|medical|patient|overdose|CPR|"
        r"transport to hospital|difficulty breathing|injury)\b", re.I)),
    ("bus", re.compile(
        r"\b(?:bus|transit|public transportation|bus route|bus driver)\b", re.I)),
    ("school", re.compile(
        r"\b(?:school|student|teacher|campus|principal|classroom|"
        r"school resource officer)\b", re.I)),
    ("traffic", re.compile(
        r"\b(?:traffic|collision|vehicle accident|motor vehicle crash|MVA|"
        r"roadway|lane closure|road closure|intersection|highway)\b", re.I)),
    ("electrical", re.compile(
        r"\b(?:electrical|electric(?:al)?|transformer|power outage|power line|"
        r"downed (?:power )?line|wires? down|substation|utility pole|"
        r"utility crew|energized line|power is out)\b", re.I)),
    ("public_works", re.compile(
        r"\b(?:public works|water main|water leak|sewer|streetlight|"
        r"snow plow|road maintenance|street maintenance)\b", re.I)),
    ("hazmat", re.compile(
        r"\b(?:hazmat|hazardous materials|chemical spill|gas leak|"
        r"carbon monoxide|propane leak)\b", re.I)),
    ("weather", re.compile(
        r"\b(?:weather|tornado|storm|flood(?:ing)?|snow|ice storm|"
        r"high winds|lightning)\b", re.I)),
)


def normalize_identifier(value: str) -> str:
    """Normalize presentation only; do not infer that similar IDs are aliases."""
    return " ".join(unicodedata.normalize("NFC", value).upper().split())


def classify_message(text: str) -> str:
    matches = [kind for kind, pattern in _MESSAGE_PATTERNS if pattern.search(text)]
    return ";".join(matches) if matches else "other_or_unclassified"


def classify_call_types(text: str) -> str:
    """Return overlapping text-only call-type labels; these labels are not edges."""
    matches = [kind for kind, pattern in _CALL_TYPE_PATTERNS if pattern.search(text)]
    return ";".join(matches) if matches else "unclassified"


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
