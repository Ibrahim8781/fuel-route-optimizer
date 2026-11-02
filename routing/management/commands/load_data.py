"""Load US places and fuel stations into the database (one-time, fully offline).

    python manage.py load_data

Stations have no coordinates in the source CSV (addresses are highway exits such
as "I-44, EXIT 283"), so each station is geocoded to its city using the places
table. No external geocoding API is called. The command is idempotent: it
replaces existing rows inside a single transaction. Restart the web server
afterwards: stations are read into memory once per process.
"""

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from routing.models import FuelStation, Place
from routing.text import US_STATES, normalize_place_name

DATA_DIR = Path(settings.BASE_DIR) / "data"


def read_places(path: Path) -> list[Place]:
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            Place(
                name=row["name"],
                state=row["state"],
                key=normalize_place_name(row["name"]),
                lat=float(row["lat"]),
                lon=float(row["lon"]),
            )
            for row in csv.DictReader(handle)
        ]


def read_stations(path: Path) -> tuple[dict[int, dict], int]:
    """Return ({opis_id: row} for US stations, keeping each station's lowest price; total rows read)."""
    stations: dict[int, dict] = {}
    total = 0
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            total += 1
            state = row["State"].strip().upper()
            if state not in US_STATES:
                continue  # Canadian provinces are in the file; the task is USA only
            opis_id = int(row["OPIS Truckstop ID"])
            price = float(row["Retail Price"])
            # 597 IDs appear more than once with different prices; a driver would pay the lowest.
            if opis_id not in stations or price < stations[opis_id]["price"]:
                stations[opis_id] = {
                    "opis_id": opis_id,
                    "name": row["Truckstop Name"].strip(),
                    "address": row["Address"].strip(),
                    "city": row["City"].strip(),
                    "state": state,
                    "price": price,
                }
    return stations, total


class Command(BaseCommand):
    help = "Load US places and fuel stations (geocoded offline to city level)."

    def add_arguments(self, parser):
        parser.add_argument("--stations-csv", type=Path, default=DATA_DIR / "fuel-prices-for-be-assessment.csv")
        parser.add_argument("--places-csv", type=Path, default=DATA_DIR / "us_places.csv")

    def handle(self, *args, **options):
        for option in ("stations_csv", "places_csv"):
            if not options[option].exists():
                raise CommandError(f"File not found: {options[option]}")

        places = read_places(options["places_csv"])
        coordinates = {(p.key, p.state): (p.lat, p.lon) for p in places}

        rows, total_rows = read_stations(options["stations_csv"])
        stations, unmatched = [], []
        for row in rows.values():
            point = coordinates.get((normalize_place_name(row["city"]), row["state"]))
            if point is None:
                unmatched.append(f"{row['city']}, {row['state']}")
                continue
            stations.append(FuelStation(**row, lat=point[0], lon=point[1]))

        with transaction.atomic():
            Place.objects.all().delete()
            Place.objects.bulk_create(places, batch_size=5000)
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=5000)

        self.stdout.write(f"Places loaded:            {len(places):,}")
        self.stdout.write(f"Price rows read:          {total_rows:,}")
        self.stdout.write(f"Unique US stations:       {len(rows):,}")
        self.stdout.write(f"Stations geocoded:        {len(stations):,}")
        if unmatched:
            self.stdout.write(f"Skipped (unknown city):   {len(unmatched):,} -> {sorted(set(unmatched))[:10]}")
        self.stdout.write(self.style.SUCCESS("Done."))
