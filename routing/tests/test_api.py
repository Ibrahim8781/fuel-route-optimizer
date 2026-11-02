"""API, map and routing-client tests. The routing provider is always mocked: no network in tests."""

import socket
import threading
import time
from unittest import mock

import numpy as np
import pytest
import requests
from django.core.cache import cache
from django.test import Client
from rest_framework.throttling import AnonRateThrottle

from routing.models import FuelStation, Place
from routing.services import ors
from routing.services.geo import haversine_miles
from routing.services.locations import Location
from routing.services.ors import Route, RoutingError, fetch_route
from routing.services.stations import get_station_index
from routing.views import RouteMapView, RoutePlanView

URL = "/api/route/"
CHICAGO = (41.85, -87.6779)
DENVER = (39.7392, -104.9903)


def straight_route(start, finish, points=200):
    lat = np.linspace(start[0], finish[0], points)
    lon = np.linspace(start[1], finish[1], points)
    distance = float(haversine_miles(*start, *finish))
    return Route(points=list(zip(lat, lon, strict=True)), distance_miles=distance, duration_seconds=distance * 60)


def point_along(fraction):
    return tuple(a + (b - a) * fraction for a, b in zip(CHICAGO, DENVER, strict=True))


@pytest.fixture(autouse=True)
def clean_caches():
    cache.clear()
    get_station_index.cache_clear()
    yield
    get_station_index.cache_clear()


@pytest.fixture
def trip_data(db):
    Place.objects.create(name="Chicago", state="IL", key="chicago", lat=CHICAGO[0], lon=CHICAGO[1])
    Place.objects.create(name="Denver", state="CO", key="denver", lat=DENVER[0], lon=DENVER[1])
    Place.objects.create(name="Atlanta", state="GA", key="atlanta", lat=33.749, lon=-84.388)
    stations = [  # (fraction of the ~920-mile route, price)
        (0.0, 3.50),
        (0.01, 3.20),
        (0.30, 3.60),
        (0.45, 2.90),
        (0.80, 3.10),
    ]
    for number, (fraction, price) in enumerate(stations, start=1):
        lat, lon = point_along(fraction)
        FuelStation.objects.create(
            opis_id=number, name=f"STOP {number}", address="I-80", city="X", state="NE", price=price, lat=lat, lon=lon
        )
    # A cheap station far from the route must be ignored.
    FuelStation.objects.create(
        opis_id=99, name="FAR AWAY", address="I-75", city="Atlanta", state="GA", price=1.00, lat=33.749, lon=-84.388
    )


@pytest.fixture
def mock_routing():
    with mock.patch("routing.services.planner.fetch_route", return_value=straight_route(CHICAGO, DENVER)) as fake:
        yield fake


def test_plan_success(client, trip_data, mock_routing):
    response = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, Colorado"})
    assert response.status_code == 200
    body = response.json()

    assert body["start"]["resolved"] == "Chicago, IL"
    assert body["finish"]["resolved"] == "Denver, CO"
    summary = body["summary"]
    assert summary["total_gallons"] == pytest.approx(summary["distance_miles"] / 10, abs=0.05)
    assert "FAR AWAY" not in [s["name"] for s in body["fuel_stops"]]
    # Cheapest near the start ($3.20) -> cheaper $2.90 at ~mile 410, fill up -> Denver (~915 mi) is
    # just out of range, so top up at the $3.10 station.
    assert [s["name"] for s in body["fuel_stops"]] == ["STOP 2", "STOP 4", "STOP 5"]
    assert body["fuel_stops"][1]["gallons"] == 48.5  # full tank minus the 15-mile reserve
    assert sum(s["cost_usd"] for s in body["fuel_stops"]) == pytest.approx(summary["total_fuel_cost_usd"], abs=0.02)
    assert body["meta"]["routing_api_calls"] == 1
    assert body["route"]["type"] == "LineString"
    assert body["map_url"].startswith("http://testserver/map/?start=Chicago")
    assert list(body)[-1] == "route"  # big geometry last


def test_stop_cost_parameter(client, trip_data, mock_routing):
    body = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO", "stop_cost": "0"}).json()
    assert body["assumptions"]["cost_per_stop_usd"] == 0
    assert "stop_cost=0" in body["map_url"]
    default = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"}).json()
    assert default["assumptions"]["cost_per_stop_usd"] == 10


def test_second_request_uses_cache(client, trip_data, mock_routing):
    client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"})
    body = client.get(URL, {"start": "chicago, il", "finish": "Denver, CO"}).json()
    assert body["meta"] == {**body["meta"], "routing_api_calls": 0, "route_cache_hit": True}
    assert mock_routing.call_count == 1


def test_map_page_reuses_cached_route(client, trip_data, mock_routing):
    client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"})
    response = client.get("/map/", {"start": "Chicago, IL", "finish": "Denver, CO"})
    assert response.status_code == 200
    assert b"STOP 4" in response.content and b"leaflet" in response.content
    assert mock_routing.call_count == 1


def test_map_page_shows_errors(client, trip_data):
    response = client.get("/map/", {"start": "Nowhere, IL", "finish": "Denver, CO"})
    assert response.status_code == 400
    assert b"Unknown city" in response.content


@pytest.mark.parametrize(
    ("params", "code"),
    [
        ({}, "invalid_request"),
        ({"start": "Chicago, IL"}, "invalid_request"),
        ({"start": "Chicago, IL", "finish": "Gotham, NY"}, "invalid_location"),
        ({"start": "Chicago, IL", "finish": "Toronto, ON"}, "invalid_location"),
        ({"start": "Chicago, IL", "finish": "Chicago, IL"}, "invalid_location"),
        ({"start": "Chicago, IL", "finish": "Denver, CO", "stop_cost": "-1"}, "invalid_request"),
        ({"start": "Chicago, IL", "finish": "Denver, CO", "stop_cost": "abc"}, "invalid_request"),
    ],
)
def test_bad_input_returns_400(client, trip_data, mock_routing, params, code):
    response = client.get(URL, params)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == code
    mock_routing.assert_not_called()


def test_no_stations_returns_422(client, trip_data, mock_routing):
    FuelStation.objects.all().delete()
    response = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_fuel_plan"


def test_routing_failure_is_passed_through(client, trip_data):
    with mock.patch("routing.services.planner.fetch_route", side_effect=RoutingError("No drivable route", 422)):
        response = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"})
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "routing_failed", "message": "No drivable route"}


def test_failed_routes_are_cached(client, trip_data):
    failure = RoutingError("No drivable route between these locations.", 422)
    with mock.patch("routing.services.planner.fetch_route", side_effect=failure) as fake:
        for _ in range(3):
            assert client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"}).status_code == 422
    assert fake.call_count == 1


def test_unknown_url_returns_json_404(client, db):
    response = client.get("/nope/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unhandled_exception_returns_json_500(trip_data):
    client = Client(raise_request_exception=False)
    with mock.patch("routing.views.plan_trip", side_effect=RuntimeError("boom")):
        response = client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "server_error"


def test_health(client, trip_data):
    assert client.get("/api/health/").json() == {"status": "ok", "stations": 6, "cache": True}


def test_framework_errors_use_the_same_shape(client, db):
    response = client.post(URL)
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


class TwoPerMinute(AnonRateThrottle):
    rate = "2/minute"


@pytest.fixture
def strict_rate_limit():
    with (
        mock.patch.object(RoutePlanView, "throttle_classes", [TwoPerMinute]),
        mock.patch.object(RouteMapView, "throttle_classes", [TwoPerMinute]),
    ):
        yield


def test_rate_limit(client, trip_data, mock_routing, strict_rate_limit):
    responses = [client.get(URL, {"start": "Chicago, IL", "finish": "Denver, CO"}) for _ in range(3)]
    assert [r.status_code for r in responses] == [200, 200, 429]
    assert responses[-1].json()["error"]["code"] == "throttled"


def test_rate_limit_ignores_spoofed_forwarded_for(client, trip_data, mock_routing, strict_rate_limit):
    trip = {"start": "Chicago, IL", "finish": "Denver, CO"}
    codes = [client.get(URL, trip, HTTP_X_FORWARDED_FOR=f"10.0.0.{n}").status_code for n in range(3)]
    assert codes == [200, 200, 429]


def test_map_page_shares_the_rate_limit(client, trip_data, mock_routing, strict_rate_limit):
    trip = {"start": "Chicago, IL", "finish": "Denver, CO"}
    client.get(URL, trip)
    client.get("/map/", trip)
    response = client.get("/map/", trip)
    assert response.status_code == 429
    assert b"Could not plan this trip" in response.content  # styled page, not plain text


def test_map_page_can_return_json(client, trip_data, mock_routing):
    response = client.get("/map/", {"start": "Chicago, IL", "finish": "Denver, CO"}, HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    assert response.json()["plan"]["summary"]["fuel_stops"] == 3


def test_health_fails_when_cache_is_down(client, trip_data):
    with mock.patch("routing.views.cache.set", side_effect=ConnectionError("redis down")):
        response = client.get("/api/health/")
    assert response.status_code == 503
    assert response.json()["cache"] is False


# --- OpenRouteService client --------------------------------------------------

START = Location("Chicago, IL", *CHICAGO)
FINISH = Location("Denver, CO", *DENVER)


def fake_response(status, payload):
    response = mock.Mock(status_code=status, text="")
    response.json.return_value = payload
    return response


def test_ors_parses_geojson():
    payload = {
        "features": [
            {
                "geometry": {"coordinates": [[-87.67, 41.85], [-104.99, 39.73]]},
                "properties": {"summary": {"distance": 1003.2, "duration": 54000}},
            }
        ]
    }
    with mock.patch.object(ors.session, "post", return_value=fake_response(200, payload)) as post:
        route = fetch_route(START, FINISH)
    assert route.points == [(41.85, -87.67), (39.73, -104.99)]  # converted to (lat, lon)
    assert route.distance_miles == 1003.2
    assert post.call_args.kwargs["json"]["coordinates"] == [[CHICAGO[1], CHICAGO[0]], [DENVER[1], DENVER[0]]]


@pytest.mark.parametrize(
    ("status", "payload", "expected_status"),
    [
        (404, {"error": {"code": 2009, "message": "Route could not be found"}}, 422),
        (400, {"error": {"code": 2003, "message": "Parameter 'units' has incorrect value"}}, 502),  # our bug
        (401, {"error": "Invalid key"}, 503),
        (429, {}, 503),
        (500, {}, 502),
        (200, {"unexpected": True}, 502),
    ],
)
def test_ors_errors(status, payload, expected_status):
    with (
        mock.patch.object(ors.session, "post", return_value=fake_response(status, payload)),
        pytest.raises(RoutingError) as error,
    ):
        fetch_route(START, FINISH)
    assert error.value.status == expected_status


def test_ors_hides_provider_details():
    payload = {"error": {"code": 2009, "message": "Unable to find a route between points 1 (-157.85 21.30) and 2"}}
    with (
        mock.patch.object(ors.session, "post", return_value=fake_response(404, payload)),
        pytest.raises(RoutingError) as error,
    ):
        fetch_route(START, FINISH)
    assert str(error.value) == "No drivable route between these locations."


def test_ors_timeout():
    with mock.patch.object(ors.session, "post", side_effect=requests.Timeout), pytest.raises(RoutingError) as error:
        fetch_route(START, FINISH)
    assert error.value.status == 504


def test_ors_requires_api_key(settings):
    settings.ORS_API_KEY = ""
    with pytest.raises(RoutingError, match="ORS_API_KEY"):
        fetch_route(START, FINISH)


# --- Real sockets: retry and timeout behaviour against a misbehaving local "provider" ----------


class FakeProvider:
    """A local HTTP server that either never answers or always answers 503 with Retry-After: 30."""

    def __init__(self, mode):
        self.mode, self.requests = mode, 0
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen()
        self.url = f"http://127.0.0.1:{self.server.getsockname()[1]}"
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            self.requests += 1
            request = b""
            while b"\r\n\r\n" not in request:
                request += conn.recv(65536)
            head, _, body = request.partition(b"\r\n\r\n")
            length = int(
                next(line.split(b":")[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length"))
            )
            while len(body) < length:
                body += conn.recv(65536)
            if self.mode == "503":
                conn.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nRetry-After: 30\r\nContent-Length: 0\r\n"
                    b"Connection: close\r\n\r\n"
                )
                conn.close()
            # mode "hang": keep the connection open and never answer


def test_hung_provider_times_out_once_with_504(settings):
    provider = FakeProvider("hang")
    settings.ORS_BASE_URL, settings.ORS_TIMEOUT_SECONDS = provider.url, 0.5
    started = time.monotonic()
    with pytest.raises(RoutingError) as error:
        fetch_route(START, FINISH)
    assert error.value.status == 504
    assert time.monotonic() - started < 2  # one read timeout, no retries
    assert provider.requests == 1


def test_provider_503_is_retried_twice_and_retry_after_is_ignored(settings):
    provider = FakeProvider("503")
    settings.ORS_BASE_URL = provider.url
    started = time.monotonic()
    with pytest.raises(RoutingError) as error:
        fetch_route(START, FINISH)
    assert error.value.status == 502
    assert provider.requests == 3  # first try + 2 retries
    assert time.monotonic() - started < 5  # backoff only, not Retry-After: 30


def test_concurrent_requests_for_a_new_trip_make_one_provider_call():
    from routing.services.planner import get_route

    def slow_route(start, finish):
        time.sleep(0.5)
        return straight_route(CHICAGO, DENVER)

    results = []
    with mock.patch("routing.services.planner.fetch_route", side_effect=slow_route) as fake:
        threads = [threading.Thread(target=lambda: results.append(get_route(START, FINISH))) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert fake.call_count == 1
    assert sorted(from_cache for _, from_cache in results) == [False, True, True, True]
