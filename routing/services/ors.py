"""OpenRouteService directions client: one request per route (retried only on transient failures)."""

import logging
import time
from dataclasses import dataclass

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from routing.services.locations import Location

logger = logging.getLogger(__name__)

# One shared session: keeps connections alive and retries twice (0.5 s, 1 s) when the provider
# drops the connection or answers 502/503/504. A real answer, including 4xx, is never retried.
session = requests.Session()
session.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=2,
            backoff_factor=0.5,
            status_forcelist=(502, 503, 504),
            allowed_methods=frozenset({"POST"}),
            raise_on_status=False,
        )
    ),
)

# ORS error codes that mean "this trip can't be routed", as opposed to a bad request from us.
NO_ROUTE_CODES = {
    2004: "The trip is longer than the routing provider allows (6,000 km).",
    2009: "No drivable route between these locations.",
    2010: "The start or finish is too far from any road.",
}


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
        response = session.post(
            url,
            json=body,
            headers={"Authorization": settings.ORS_API_KEY},
            timeout=settings.ORS_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise RoutingError("The routing service timed out.", status=504) from exc
    except requests.RequestException as exc:
        logger.warning("ORS request failed: %s", exc)
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
    if response.status_code != 200:
        code, detail = _error_details(response)
        # The provider's own message stays in our logs; clients get a stable message of ours.
        logger.warning("ORS error HTTP %s code %s: %s", response.status_code, code, detail)
        if code in NO_ROUTE_CODES:
            raise RoutingError(NO_ROUTE_CODES[code], status=422)
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


def _error_details(response: requests.Response) -> tuple[int | None, str]:
    try:
        error = response.json()["error"]
        return (error.get("code"), str(error.get("message"))) if isinstance(error, dict) else (None, str(error))
    except (ValueError, KeyError, TypeError):
        return None, response.text[:200]
