import numpy as np
import pytest

from routing.models import Place
from routing.services.geo import cumulative_miles, haversine_miles, resample, stations_near_route, to_unit_vectors
from routing.services.locations import LocationError, resolve_location

CHICAGO = (41.8781, -87.6298)
ST_LOUIS = (38.6270, -90.1994)


def test_haversine_known_distance():
    # New York City to Los Angeles is about 2,445 miles (3,936 km) as the crow flies.
    assert haversine_miles(40.7128, -74.0060, 34.0522, -118.2437) == pytest.approx(2445, abs=5)


def test_resample_spacing_and_length():
    line = np.array([CHICAGO, ST_LOUIS])
    points, markers = resample(line, step_miles=1.0)
    assert markers[0] == 0 and markers[-1] == pytest.approx(cumulative_miles(line)[-1])
    assert np.diff(markers).max() <= 1.0
    np.testing.assert_allclose(points[0], CHICAGO)
    np.testing.assert_allclose(points[-1], ST_LOUIS)


def test_stations_near_route_finds_only_close_stations_in_route_order():
    # A straight east-west route along latitude 40 from lon -100 to -99 (~53 miles).
    route = np.array([(40.0, -100.0), (40.0, -99.0)])
    points, markers = resample(route, 1.0)
    stations = np.array(
        [
            (40.03, -99.2),  # ~2 miles north, near the end
            (40.0, -99.9),  # on the route, near the start
            (40.5, -99.5),  # ~35 miles away
        ]
    )
    hits, miles, offsets = stations_near_route(points, markers, to_unit_vectors(stations[:, 0], stations[:, 1]), 5)

    assert hits.tolist() == [1, 0]  # sorted by position along the route
    assert miles[0] == pytest.approx(5.3, abs=0.6)
    assert miles[1] == pytest.approx(42.4, abs=0.6)
    assert offsets[0] < 0.6 and offsets[1] == pytest.approx(2.07, abs=0.3)


@pytest.fixture
def places(db):
    Place.objects.create(name="Saint Louis", state="MO", key="saintlouis", lat=38.6445, lon=-90.2496)
    Place.objects.create(name="Dallas", state="TX", key="dallas", lat=32.79, lon=-96.77)


@pytest.mark.parametrize("text", ["St. Louis, MO", "saint louis, mo", "St Louis,Missouri"])
def test_resolve_city_variants(places, text):
    location = resolve_location(text)
    assert location.label == "Saint Louis, MO"
    assert (location.lat, location.lon) == (38.6445, -90.2496)


def test_resolve_coordinates(db):
    location = resolve_location(" 32.7767, -96.7970 ")
    assert (location.lat, location.lon) == (32.7767, -96.797)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("Dallas", "not understood"),
        ("Toronto, ON", "not a US state"),
        ("Atlantis, TX", "Unknown city"),
        ("51.05,-114.07", "outside the USA"),  # Calgary
        ("43.65,-79.38", "outside the USA"),  # Toronto: inside the rough US box, but far from any US city
        ("48.85,2.35", "outside the USA"),  # Paris
    ],
)
def test_resolve_rejects_bad_input(places, text, message):
    with pytest.raises(LocationError, match=message):
        resolve_location(text)
