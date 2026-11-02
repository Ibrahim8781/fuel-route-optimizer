"""OpenRouteService directions client: exactly one HTTP request per route."""

import logging
import time
from dataclasses import dataclass

import requests
from django.conf import settings

from routing.services.locations import Location

logger = logging.getLogger(__name__)


class RoutingError(Exception):
    """The routing provider could not produce a route. `status` is the HTTP status to return."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Route:
    points: list[tuple[float, float]]  # [(lat, lon), ...] along the road
    distance_miles: float
    duration_seconds: float


def fetch_route(start: Location, finish: Location) -> Route:
    if not settings.ORS_API_KEY:
        raise RoutingError("Routing is not configured: set ORS_API_KEY.", status=503)

    url = f"{settings.ORS_BASE_URL}/v2/directions/{settings.ORS_PROFILE}/geojson"
    body = {
        "coordinates": [[start.lon, start.lat], [finish.lon, finish.lat]],  # ORS expects [lon, lat]
        "units": "mi",
        "instructions": False,
        "radiuses": [-1, -1],  # snap city centre points to the nearest road, however far
    }
    started = time.perf_counter()
    try:
        response = requests.post(
            url,
            json=body,
            headers={"Authorization": settings.ORS_API_KEY},
            timeout=settings.ORS_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise RoutingError("The routing service timed out.", status=504) from exc
    except requests.RequestException as exc:
        raise RoutingError("The routing service is unreachable.") from exc
    logger.info(
        "ORS directions %s -> %s: HTTP %s in %.0f ms",
        start.label,
        finish.label,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )

    if response.status_code == 429:
        raise RoutingError("Routing quota exceeded; try again shortly.", status=503)
    if response.status_code in (401, 403):
        raise RoutingError("The routing service rejected the API key.", status=503)
    if 400 <= response.status_code < 500:
        # e.g. no road connection, or route longer than the provider allows (6,000 km).
        raise RoutingError(f"No drivable route found: {_error_message(response)}", status=422)
    if response.status_code != 200:
        raise RoutingError(f"The routing service failed (HTTP {response.status_code}).")

    try:
        feature = response.json()["features"][0]
        summary = feature["properties"]["summary"]
        points = [(lat, lon) for lon, lat, *_ in feature["geometry"]["coordinates"]]
        return Route(
            points=points,
            distance_miles=float(summary.get("distance", 0.0)),
            duration_seconds=float(summary.get("duration", 0.0)),
        )
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RoutingError("The routing service returned an unexpected response.") from exc


def _error_message(response: requests.Response) -> str:
    try:
        return str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP {response.status_code}"
