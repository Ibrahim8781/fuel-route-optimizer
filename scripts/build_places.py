"""Rebuild data/us_places.csv: one coordinate per US city, for offline geocoding.

Source: the `zipcodes` package (MIT), whose coordinates come from GeoNames (CC BY 4.0).
Each city gets the median of its ZIP code centroids, which is robust to the odd
far-away ZIP. Alternate city names ("acceptable cities") are added only when no
primary city already uses that name.

Usage (dev dependency):  pip install zipcodes && python scripts/build_places.py
"""

import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from routing.text import US_STATES, normalize_place_name  # noqa: E402

OUTPUT = ROOT / "data" / "us_places.csv"


def build_places(records: list[dict]) -> list[tuple[str, str, float, float]]:
    """Group ZIP records into (name, state, lat, lon) rows, sorted by state then name."""
    primary: dict[tuple[str, str], list] = defaultdict(list)
    alias: dict[tuple[str, str], list] = defaultdict(list)

    for record in records:
        state = record["state"]
        if state not in US_STATES or record["zip_code_type"] == "MILITARY":
            continue
        point = (float(record["lat"]), float(record["long"]))
        primary[(record["city"], state)].append(point)
        for name in record.get("acceptable_cities") or []:
            alias[(name, state)].append(point)

    rows: dict[tuple[str, str], tuple[str, str, float, float]] = {}
    for group in (primary, alias):  # primary names win over aliases
        for (name, state), points in group.items():
            key = (normalize_place_name(name), state)
            if key in rows:
                continue
            lat = statistics.median(p[0] for p in points)
            lon = statistics.median(p[1] for p in points)
            rows[key] = (name.title() if name.isupper() else name, state, round(lat, 5), round(lon, 5))

    return sorted(rows.values(), key=lambda row: (row[1], row[0]))


def main() -> None:
    import zipcodes

    rows = build_places(zipcodes.list_all())
    with OUTPUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["name", "state", "lat", "lon"])
        writer.writerows(rows)
    print(f"Wrote {len(rows)} places to {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
