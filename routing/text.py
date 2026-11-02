"""Text helpers shared by the data loader and the location resolver."""

import re

# Two-letter codes for the 50 states plus DC. Canadian provinces in the CSV are excluded.
US_STATES = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
}
_STATE_NAMES = {name.lower(): code for code, name in US_STATES.items()}

# Abbreviations that appear as the first word of US place names.
_PREFIXES = {"st": "saint", "ste": "sainte", "ft": "fort", "mt": "mount", "pt": "port"}


def normalize_place_name(name: str) -> str:
    """Canonical lookup key for a city name: lowercase, St/Ft/Mt expanded, no punctuation or spaces.

    "St. Louis", "ST LOUIS" and "Saint Louis" all become "saintlouis";
    "De Forest" and "DeForest" both become "deforest".
    """
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("'", "")).split()
    if words and words[0] in _PREFIXES:
        words[0] = _PREFIXES[words[0]]
    return "".join(words)


def to_state_code(value: str) -> str | None:
    """Return the two-letter code for "TX", "tx" or "Texas"; None if not a US state."""
    cleaned = value.strip()
    if cleaned.upper() in US_STATES:
        return cleaned.upper()
    return _STATE_NAMES.get(cleaned.lower())
