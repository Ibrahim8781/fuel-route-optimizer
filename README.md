# Fuel Route Optimizer

A Django REST API that plans a truck trip between two US locations. It returns the route on a map, the **cheapest places to refuel** for a vehicle with a **500-mile range**, and the **total fuel cost at 10 mpg**, using the provided fuel price list.

- **One routing API call per new trip**, and zero for repeated trips (cached).
- **Fast:** about 35 ms of server-side work per new trip, plus the routing provider's response time. A cached trip takes about 6 ms.
- **Cost-optimal fuel plan:** a greedy algorithm, checked against a linear-program solver in the tests.

> Design decisions, architecture and the algorithm are explained in **[docs/DESIGN.md](docs/DESIGN.md)**.

---

## Quick start

Requires **Python 3.12+** and a free **[OpenRouteService API key](https://openrouteservice.org/dev/#/signup)**.

```bash
git clone https://github.com/<your-username>/fuel-route-optimizer.git
cd fuel-route-optimizer

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env                 # Windows: copy .env.example .env
# edit .env and set ORS_API_KEY=<your key>

python manage.py migrate
python manage.py load_data           # one-time: loads places + stations (~3 s, no network)
python manage.py runserver
```

`load_data` prints what it loaded:

```
Places loaded:            39,176
Price rows read:          8,151
Unique US stations:       6,626
Stations geocoded:        6,626
```

## Using the API

```
GET /api/route/?start=<location>&finish=<location>
```

A location can be `City, ST` (`Chicago, IL`), `City, State` (`Chicago, Illinois`) or `lat,lon` (`41.8781,-87.6298`).

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA"
```

Response (trimmed, values illustrative):

```json
{
  "start":  {"query": "New York, NY", "resolved": "New York, NY", "lat": 40.7571, "lon": -73.9778},
  "finish": {"query": "Los Angeles, CA", "resolved": "Los Angeles, CA", "lat": 34.0522, "lon": -118.2494},
  "summary": {
    "distance_miles": 2790.4,
    "duration_hours": 45.1,
    "fuel_stops": 8,
    "total_gallons": 279.04,
    "total_fuel_cost_usd": 846.12,
    "average_price_per_gallon": 3.032
  },
  "assumptions": {
    "max_range_miles": 500, "miles_per_gallon": 10, "station_corridor_miles": 5,
    "starting_fuel": "empty; first fill-up at the cheapest station within 25 miles (...)"
  },
  "fuel_stops": [
    {
      "stop": 1, "station_id": 1234, "name": "BOLLA MARKET",
      "address": "...", "city": "Elizabeth", "state": "NJ", "lat": 40.66785, "lon": -74.2161,
      "price_per_gallon": 3.099, "mile_marker": 11.5, "miles_off_route": 2.4,
      "gallons": 51.15, "cost_usd": 158.53
    }
  ],
  "meta": {"routing_api_calls": 1, "route_cache_hit": false, "stations_on_route": 230, "elapsed_ms": 812.4},
  "map_url": "http://127.0.0.1:8000/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "route": {"type": "LineString", "coordinates": [[-73.9778, 40.7571], "..."]}
}
```

**Map:** open `map_url` in a browser to see the route and numbered fuel stops, drawn with Leaflet and OpenStreetMap. The map reuses the cached route, so it doesn't call the routing API again.

**Errors** always use the same shape: `{"error": {"code": "...", "message": "..."}}`.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` | `start` or `finish` missing |
| 400 | `invalid_location` | unknown city, non-US state or coordinates, start = finish |
| 422 | `routing_failed` | no drivable route (e.g. to Hawaii) |
| 422 | `no_fuel_plan` | no station within 500 miles of the start, or a gap longer than 500 miles |
| 429 | `throttled` | more than 60 requests per minute from one IP |
| 502 / 503 / 504 | `routing_failed` | routing provider down, quota or key problem, timeout |

A Postman collection with example requests and tests is in [`postman/`](postman/fuel-route-optimizer.postman_collection.json).

## Assumptions

- **Fuel stations are geocoded to their city.** The CSV has no coordinates, and its addresses are highway exits such as `I-44, EXIT 283`. Each station gets its city's coordinate, taken from a bundled US places table. No geocoding API is used.
- **On the route** means within **5 miles** of the road. The detour to the station isn't added to the distance.
- **The truck starts empty.** Its first fill-up is the cheapest station within the first 25 miles (or, if there is none, the first station on the route). The fuel used to get there is billed at that station, so total gallons always equal distance ÷ 10 mpg.
- **Any amount can be bought at a stop.** The truck arrives at the destination with an empty tank.
- **Duplicate station IDs** in the CSV (597 of them) keep their **lowest price**. **Canadian stations** are excluded.

All of these are configurable through environment variables (see `.env.example`).

## Tests and code quality

```bash
pytest            # 91 tests, no network needed (the routing API is mocked)
ruff check .      # lint
ruff format .     # format
```

The tests cover the optimizer (hand-checked cases, plus 40 random routes compared with an LP solver), geometry, location parsing, data loading, the API's success and error paths, caching, and the routing client's failure modes. CI runs lint and tests on every push (`.github/workflows/ci.yml`).

## Project layout

```
fuelroute/                  Django project (settings from env, URLs)
routing/
  models.py                 Place, FuelStation
  views.py                  /api/route/ (DRF) and /map/
  services/
    planner.py              orchestration + route cache
    optimizer.py            cheapest fuel-stop algorithm (pure Python)
    geo.py                  haversine, route resampling, KD-tree corridor search
    stations.py             in-memory station index
    locations.py            "City, ST" / "lat,lon" -> coordinate (offline)
    ors.py                  OpenRouteService client
  management/commands/load_data.py
  templates/routing/map.html
data/                       fuel prices (provided) + us_places.csv
scripts/build_places.py     rebuilds us_places.csv
docs/DESIGN.md              design and walkthrough
```

## Production notes

- Set `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`, and serve with a WSGI server such as gunicorn.
- Set `REDIS_URL` so all workers share one route cache.
- Requests are rate limited (default `60/minute` per IP) to protect the free routing quota.

## Data credits

Fuel prices are from the assessment. City coordinates come from [GeoNames](https://www.geonames.org/) (CC BY 4.0) via the [`zipcodes`](https://github.com/seanpianka/zipcodes) package. Routing is by [OpenRouteService](https://openrouteservice.org/), and map tiles are © OpenStreetMap contributors.
