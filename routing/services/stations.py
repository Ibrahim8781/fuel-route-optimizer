"""In-memory index of all fuel stations, loaded from the database once per process."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from routing.models import FuelStation
from routing.services.geo import to_unit_vectors


@dataclass(frozen=True)
class StationIndex:
    records: list[dict]  # station details, same order as the arrays below
    prices: np.ndarray
    vectors: np.ndarray  # unit-sphere coordinates for the KD-tree query

    def __len__(self) -> int:
        return len(self.records)


@lru_cache(maxsize=1)
def get_station_index() -> StationIndex:
    """~6.6k stations fit easily in memory; reading them per request would waste time."""
    records = list(
        FuelStation.objects.order_by("id").values("opis_id", "name", "address", "city", "state", "price", "lat", "lon")
    )
    if not records:
        return StationIndex(records=[], prices=np.empty(0), vectors=np.empty((0, 3)))
    lat = np.array([r["lat"] for r in records])
    lon = np.array([r["lon"] for r in records])
    return StationIndex(
        records=records,
        prices=np.array([r["price"] for r in records]),
        vectors=to_unit_vectors(lat, lon),
    )
