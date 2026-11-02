import pytest

from routing.services.locations import _place_tree
from routing.services.stations import get_station_index


@pytest.fixture(autouse=True)
def clear_in_memory_indexes():
    """Station and place indexes are cached per process; reset them so each test sees its own data."""
    get_station_index.cache_clear()
    _place_tree.cache_clear()
    yield
    get_station_index.cache_clear()
    _place_tree.cache_clear()
