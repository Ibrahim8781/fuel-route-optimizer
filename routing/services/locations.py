"""Turn user input ("Chicago, IL", "Dallas, Texas" or "41.88,-87.63") into a US coordinate, offline."""

import re
from dataclasses import dataclass

from routing.models import Place
from routing.text import normalize_place_name, to_state_code

_LAT_LON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

# Rough bounding boxes (south, north, west, east) for the lower 48, Alaska and Hawaii.
_US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),
    (51.0, 71.6, -180.0, -129.9),
    (18.8, 22.4, -160.6, -154.6),
)


class LocationError(ValueError):
    """Input that cannot be resolved to a location in the USA."""


@dataclass(frozen=True)
class Location:
    label: str
    lat: float
    lon: float


def is_in_usa(lat: float, lon: float) -> bool:
    return any(south <= lat <= north and west <= lon <= east for south, north, west, east in _US_BOXES)


def resolve_location(text: str) -> Location:
    text = text.strip()

    if match := _LAT_LON.match(text):
        lat, lon = float(match[1]), float(match[2])
        if not is_in_usa(lat, lon):
            raise LocationError(f"'{text}' is outside the USA. Coordinates must be 'lat,lon' within the USA.")
        return Location(label=f"{lat:.5f},{lon:.5f}", lat=lat, lon=lon)

    city, _, state = text.rpartition(",")
    if not city.strip() or not state.strip():
        raise LocationError(f"'{text}' is not understood. Use 'City, ST' (e.g. 'Chicago, IL') or 'lat,lon'.")

    code = to_state_code(state)
    if code is None:
        raise LocationError(f"'{state.strip()}' is not a US state. Use a two-letter code such as 'TX'.")

    place = Place.objects.filter(key=normalize_place_name(city), state=code).first()
    if place is None:
        raise LocationError(f"Unknown city '{city.strip()}, {code}'.")
    return Location(label=f"{place.name}, {place.state}", lat=place.lat, lon=place.lon)
