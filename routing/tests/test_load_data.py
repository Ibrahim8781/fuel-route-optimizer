import pytest
from django.core.management import CommandError, call_command

from routing.models import FuelStation, Place
from routing.text import normalize_place_name, to_state_code

PLACES_CSV = """name,state,lat,lon
Saint Louis,MO,38.6445,-90.2496
Deforest,WI,43.2311,-89.3454
Big Cabin,OK,36.5378,-95.2214
"""

STATIONS_CSV = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.007333
20,PILOT TRAVEL CENTER #1243,"I-70, EXIT 1",St. Louis,MO,930,3.899
20,PILOT #1243,"I-70, EXIT 1",St. Louis,MO,930,3.799
30,KWIK TRIP,US-51,De Forest,WI,420,3.10
40,CANADIAN STOP,HWY 1,Calgary,AB,1,1.50
50,NOWHERE STOP,I-10,Atlantis,TX,1,2.99
"""


@pytest.fixture
def csv_files(tmp_path):
    places = tmp_path / "places.csv"
    stations = tmp_path / "stations.csv"
    places.write_text(PLACES_CSV)
    stations.write_text(STATIONS_CSV)
    return places, stations


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("St. Louis", "saintlouis"),
        ("SAINT LOUIS", "saintlouis"),
        ("Ft Worth", "fortworth"),
        ("De Forest", "deforest"),
        ("Coeur d'Alene", "coeurdalene"),
    ],
)
def test_normalize_place_name(raw, expected):
    assert normalize_place_name(raw) == expected


def test_to_state_code():
    assert to_state_code("tx") == "TX"
    assert to_state_code(" Texas ") == "TX"
    assert to_state_code("ON") is None  # Canadian province
    assert to_state_code("Narnia") is None


@pytest.mark.django_db
def test_load_data_filters_dedupes_and_geocodes(csv_files, capsys):
    places, stations = csv_files
    call_command("load_data", places_csv=places, stations_csv=stations)

    assert Place.objects.count() == 3
    # Canadian station dropped, unknown city skipped, duplicate ID collapsed.
    assert set(FuelStation.objects.values_list("opis_id", flat=True)) == {7, 20, 30}

    pilot = FuelStation.objects.get(opis_id=20)
    assert pilot.price == pytest.approx(3.799)  # lowest of the duplicate prices
    assert (pilot.lat, pilot.lon) == (38.6445, -90.2496)  # "St. Louis" matched "Saint Louis"

    output = capsys.readouterr().out
    assert "Skipped (unknown city):   1" in output


@pytest.mark.django_db
def test_load_data_is_idempotent(csv_files):
    places, stations = csv_files
    call_command("load_data", places_csv=places, stations_csv=stations)
    call_command("load_data", places_csv=places, stations_csv=stations)
    assert FuelStation.objects.count() == 3


def test_load_data_missing_file(tmp_path):
    with pytest.raises(CommandError, match="File not found"):
        call_command("load_data", places_csv=tmp_path / "nope.csv")
