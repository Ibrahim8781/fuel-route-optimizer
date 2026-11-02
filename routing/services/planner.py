"""Orchestrates a trip: resolve locations -> route (cached) -> stations on route -> cheapest fuel plan."""

import logging
import time

import numpy as np
from django.conf import settings
from django.core.cache import cache

from routing.services.geo import resample, stations_near_route
from routing.services.locations import Location, LocationError, resolve_location
from routing.services.optimizer import Candidate, plan_fuel_stops
from routing.services.ors import RoutingError, fetch_route, worst_case_seconds
from routing.services.stations import get_station_index

ROUTE_STEP_MILES = 1.0  # spacing of route points used for matching and for the map

logger = logging.getLogger(__name__)


def plan_trip(start_text: str, finish_text: str, stop_cost: float | None = None) -> dict:
    started = time.perf_counter()
    stop_cost = settings.FUEL_STOP_COST_USD if stop_cost is None else stop_cost
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
        max_range=settings.FUEL_MAX_RANGE_MILES - settings.FUEL_RESERVE_MILES,
        mpg=settings.FUEL_MPG,
        start_window=settings.FUEL_START_WINDOW_MILES,
        stop_cost=stop_cost,
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
            "reserve_miles": settings.FUEL_RESERVE_MILES,
            "miles_per_gallon": settings.FUEL_MPG,
            "station_corridor_miles": settings.FUEL_CORRIDOR_MILES,
            "cost_per_stop_usd": stop_cost,
            "starting_fuel": (
                "just enough to reach the first stop (the cheapest station within "
                f"{settings.FUEL_START_WINDOW_MILES:g} miles, or the first on the route), billed at that stop"
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
    all the planner and the map need and keeps cache entries small. Trips with
    no route are cached for an hour, and concurrent requests for the same new
    trip wait for one provider call instead of each making their own.
    """
    key = f"route:v2:{settings.ORS_PROFILE}:{start.lat:.5f},{start.lon:.5f}:{finish.lat:.5f},{finish.lon:.5f}"
    if (cached := _cached_route(key)) is not None:
        return cached, True

    # Waiters wait as long as one complete provider call can take, and the lock outlives that,
    # so a slow provider still gets one call per trip rather than one per waiting request.
    wait = worst_case_seconds()
    lock = f"{key}:lock"
    if not cache.add(lock, True, timeout=int(wait) + 5):
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            time.sleep(0.2)
            if (cached := _cached_route(key)) is not None:
                return cached, True
            if cache.get(lock) is None:  # the other request failed without caching anything
                break
        logger.warning("No result from the concurrent route request; fetching it here.")

    try:
        try:
            route = fetch_route(start, finish)
        except RoutingError as exc:
            if exc.status == 422:  # the trip itself is impossible; don't ask again for a while
                cache.set(key, {"error": str(exc)}, settings.FAILED_ROUTE_CACHE_SECONDS)
            raise
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
    finally:
        cache.delete(lock)


def _cached_route(key: str) -> dict | None:
    cached = cache.get(key)
    if cached is not None and "error" in cached:
        raise RoutingError(cached["error"], status=422)
    return cached


def _location_json(query: str, location: Location) -> dict:
    return {"query": query, "resolved": location.label, "lat": location.lat, "lon": location.lon}
