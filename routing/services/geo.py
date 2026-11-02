"""Vectorised geometry helpers (numpy). All distances are in miles, coordinates in degrees."""

import numpy as np
from scipy.spatial import cKDTree

EARTH_RADIUS_MILES = 3958.8


def haversine_miles(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance; accepts scalars or arrays."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def cumulative_miles(points: np.ndarray) -> np.ndarray:
    """Distance from the first point to every point along a polyline of shape (n, 2) [lat, lon]."""
    steps = haversine_miles(points[:-1, 0], points[:-1, 1], points[1:, 0], points[1:, 1])
    return np.concatenate(([0.0], np.cumsum(steps)))


def resample(points: np.ndarray, step_miles: float) -> tuple[np.ndarray, np.ndarray]:
    """Evenly spaced points along a polyline, plus each point's mile marker.

    Routing APIs return many points on curves and few on long straight
    highways. Even spacing gives a light map payload and makes every
    nearest-point lookup accurate to within half a step.
    """
    miles = cumulative_miles(points)
    total = miles[-1]
    count = max(int(np.ceil(total / step_miles)), 1) + 1
    markers = np.linspace(0.0, total, count)
    lat = np.interp(markers, miles, points[:, 0])
    lon = np.interp(markers, miles, points[:, 1])
    return np.column_stack((lat, lon)), markers


def to_unit_vectors(lat, lon) -> np.ndarray:
    """Points on the unit sphere, so straight-line (chord) distance tracks great-circle distance."""
    lat, lon = np.radians(lat), np.radians(lon)
    return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))


def stations_near_route(
    route_points: np.ndarray,
    route_markers: np.ndarray,
    station_vectors: np.ndarray,
    radius_miles: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Find stations within `radius_miles` of the route.

    Builds a KD-tree over the (evenly spaced) route points and asks, for every
    station, "which route point is closest, if any within the radius?".
    That is O(R log R) to build plus O(S log R) to query, instead of comparing
    every station with every route point (O(S x R)).

    Returns (station indices, mile marker of each station along the route,
    miles from the route), sorted by mile marker.
    """
    tree = cKDTree(to_unit_vectors(route_points[:, 0], route_points[:, 1]))
    max_chord = 2 * np.sin(radius_miles / (2 * EARTH_RADIUS_MILES))
    chord, nearest = tree.query(station_vectors, distance_upper_bound=max_chord)

    hits = np.flatnonzero(np.isfinite(chord))
    offsets = 2 * EARTH_RADIUS_MILES * np.arcsin(chord[hits] / 2)
    markers = route_markers[nearest[hits]]

    order = np.argsort(markers, kind="stable")
    return hits[order], markers[order], offsets[order]
