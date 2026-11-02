"""Orchestrates a trip: resolve locations -> route (cached) -> stations on route -> cheapest fuel plan."""

import time

import numpy as np
from django.conf import settings
from django.core.cache import cache

from routing.services.geo import resample, stations_near_route
from routing.services.locations import Location, LocationError, resolve_location
from routing.services.optimizer import Candidate, plan_fuel_stops
from routing.services.ors import fetch_route
from routing.services.stations import get_station_index

ROUTE_STEP_MILES = 1.0  # spacing of route points used for matching and for the map


def plan_trip(start_text: str, finish_text: str) -> dict:
    started = time.perf_counter()
    start, finish = resolve_location(start_text), resolve_location(finish_text)
    if (start.lat, start.lon) == (finish.lat, finish.lon):
        raise LocationError("Start and finish are the same place.")

    route, from_cache = get_route(start, finish)
    points = np.array(route["points"])
    markers = np.array(route["markers"])
    distance = route["distance_miles"]

    index = get_station_index()
    hits, miles, offsets = stations_near_route(points, markers, index.vectors, settings.FUEL_CORRIDOR_MILES)
    candidates = [Candidate(mile=float(m), price=float(index.prices[i])) for i, m in zip(hits, miles, strict=True)]

    plan = plan_fuel_stops(
        candidates,
        distance,
        max_range=settings.FUEL_MAX_RANGE_MILES,
        mpg=settings.FUEL_MPG,
        start_window=settings.FUEL_START_WINDOW_MILES,
    )

    stops = []
    for number, purchase in enumerate(plan.purchases, start=1):
        station = index.records[hits[purchase.index]]
        stops.append(
            {
                "stop": number,
                "station_id": station["opis_id"],
                "name": station["name"],
                "address": station["address"],
                "city": station["city"],
                "state": station["state"],
                "lat": station["lat"],
                "lon": station["lon"],
                "price_per_gallon": round(station["price"], 3),
                "mile_marker": round(candidates[purchase.index].mile, 1),
                "miles_off_route": round(float(offsets[purchase.index]), 1),
                "gallons": round(purchase.gallons, 2),
                "cost_usd": round(purchase.cost, 2),
            }
        )

    return {
        "start": _location_json(start_text, start),
        "finish": _location_json(finish_text, finish),
        "summary": {
            "distance_miles": round(distance, 1),
            "duration_hours": round(route["duration_seconds"] / 3600, 1),
            "fuel_stops": len(stops),
            "total_gallons": round(plan.total_gallons, 2),
            "total_fuel_cost_usd": round(plan.total_cost, 2),
            "average_price_per_gallon": round(plan.total_cost / plan.total_gallons, 3) if plan.total_gallons else 0,
        },
        "assumptions": {
            "max_range_miles": settings.FUEL_MAX_RANGE_MILES,
            "miles_per_gallon": settings.FUEL_MPG,
            "station_corridor_miles": settings.FUEL_CORRIDOR_MILES,
            "starting_fuel": (
                f"empty; first fill-up at the cheapest station within {settings.FUEL_START_WINDOW_MILES:g} miles "
                "(or the first station on the route), billed for the miles driven to reach it"
            ),
        },
        "fuel_stops": stops,
        "meta": {
            "routing_api_calls": 0 if from_cache else 1,
            "route_cache_hit": from_cache,
            "stations_on_route": len(candidates),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        },
        "route": {
            "type": "LineString",
            "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in points.tolist()],
        },
    }


def get_route(start: Location, finish: Location) -> tuple[dict, bool]:
    """Route between two points, from cache when possible. Returns (route, from_cache).

    Only the resampled geometry is cached (about one point per mile), which is
    all the planner and the map need and keeps cache entries small.
    """
    key = f"route:v1:{settings.ORS_PROFILE}:{start.lat:.5f},{start.lon:.5f}:{finish.lat:.5f},{finish.lon:.5f}"
    if (cached := cache.get(key)) is not None:
        return cached, True

    route = fetch_route(start, finish)
    points, markers = resample(np.array(route.points), ROUTE_STEP_MILES)
    # Scale our haversine mile markers to the provider's road distance so both agree at the destination.
    if markers[-1] > 0:
        markers = markers * (route.distance_miles / markers[-1])
    value = {
        "points": points.tolist(),
        "markers": markers.tolist(),
        "distance_miles": route.distance_miles,
        "duration_seconds": route.duration_seconds,
    }
    cache.set(key, value, settings.ROUTE_CACHE_SECONDS)
    return value, False


def _location_json(query: str, location: Location) -> dict:
    return {"query": query, "resolved": location.label, "lat": location.lat, "lon": location.lon}
