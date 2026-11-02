# Fuel Route Optimizer

A Django REST API that plans a truck trip between two US locations. It returns the route on a map, the **cheapest places to refuel** for a vehicle with a **500-mile range**, and the **total fuel cost at 10 mpg**, using the provided fuel price list.

- **One routing API call per new trip**, and zero for repeated trips (cached).
- **Fast:** about 25 ms of our own work for a coast-to-coast trip. The routing provider adds 1–2 s, but only the first time a trip is requested.
- **Optimal fuel plan:** dynamic programming that minimises fuel cost plus a small cost per stop ($10 by default; `stop_cost=0` for the cheapest fuel). It's checked against LP/MILP solvers in the tests.

> Design decisions, architecture and the algorithm are explained in **[docs/DESIGN.md](docs/DESIGN.md)**.

---

## Quick start

Requires **Python 3.12+** and a free **[OpenRouteService API key](https://openrouteservice.org/dev/#/signup)**.

```bash
git clone <this-repo-url> fuel-route-optimizer
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
GET /api/route/?start=<location>&finish=<location>[&stop_cost=<usd>]
```

A location can be `City, ST` (`Chicago, IL`), `City, State` (`Chicago, Illinois`) or `lat,lon` (`41.8781,-87.6298`).
`stop_cost` is optional (default `10`). It's the dollar cost of making a stop; `0` returns the cheapest fuel however many stops that takes.

```bash
curl "http://127.0.0.1:8000/api/route/?start=New%20York,%20NY&finish=Los%20Angeles,%20CA"
```

Response for New York → Los Angeles (real run, trimmed):

```json
{
  "start":  {"query": "New York, NY", "resolved": "New York, NY", "lat": 40.7571, "lon": -73.9778},
  "finish": {"query": "Los Angeles, CA", "resolved": "Los Angeles, CA", "lat": 34.0522, "lon": -118.2494},
  "summary": {
    "distance_miles": 2791.5,
    "duration_hours": 64.6,
    "fuel_stops": 7,
    "total_gallons": 279.15,
    "total_fuel_cost_usd": 862.01,
    "average_price_per_gallon": 3.088
  },
  "assumptions": {
    "max_range_miles": 500.0, "reserve_miles": 15.0, "miles_per_gallon": 10.0, "station_corridor_miles": 5.0,
    "cost_per_stop_usd": 10.0, "starting_fuel": "just enough to reach the first stop (...), billed at that stop"
  },
  "fuel_stops": [
    {
      "stop": 1, "station_id": 62790, "name": "7-ELEVEN #40084", "address": "US-46/US-1/US-9",
      "city": "Palisades Park", "state": "NJ", "lat": 40.8462, "lon": -73.9954,
      "price_per_gallon": 3.099, "mile_marker": 5.1, "miles_off_route": 4.9, "gallons": 38.81, "cost_usd": 120.26
    },
    {
      "stop": 2, "station_id": 72445, "name": "SHEETZ #639", "address": "I-80 Exit 223",
      "city": "Youngstown", "state": "OH", "lat": 41.096, "lon": -80.6553,
      "price_per_gallon": 3.059, "mile_marker": 388.1, "miles_off_route": 4.0, "gallons": 46.3, "cost_usd": 141.63
    }
  ],
  "meta": {"routing_api_calls": 1, "route_cache_hit": false, "stations_on_route": 372, "elapsed_ms": 2115.8},
  "map_url": "http://127.0.0.1:8000/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "route": {"type": "LineString", "coordinates": [[-73.97777, 40.75714], "..."]}
}
```

Repeat the same request and `meta` shows `"routing_api_calls": 0, "route_cache_hit": true`, answered in about 25 ms.

**Map:** open `map_url` in a browser to see the route and numbered fuel stops, drawn with Leaflet on CARTO's free OpenStreetMap basemap. The map reuses the cached route, so it doesn't call the routing API again.

**Errors** always use the same shape: `{"error": {"code": "...", "message": "..."}}`.

| Status | Code | When |
|---|---|---|
| 400 | `invalid_request` | `start` or `finish` missing, or `stop_cost` not a number from 0 to 1000 |
| 400 | `invalid_location` | unknown city, non-US state or coordinates, start = finish |
| 422 | `routing_failed` | no drivable route (e.g. to Hawaii) |
| 422 | `no_fuel_plan` | no station within 500 miles of the start, or a gap longer than 500 miles |
| 429 | `throttled` | more than 60 requests per minute from one IP (API and map combined) |
| 404 / 500 | `not_found` / `server_error` | unknown URL / unexpected failure (logged with traceback) |
| 502 / 503 / 504 | `routing_failed` | routing provider down, quota or key problem, timeout |

A Postman collection with example requests and tests is in [`postman/`](postman/fuel-route-optimizer.postman_collection.json).

## Assumptions

- **Fuel stations are geocoded to their city.** The CSV has no coordinates, and its addresses are highway exits such as `I-44, EXIT 283`. Each station gets its city's coordinate, taken from a bundled US places table. No geocoding API is used.
- **On the route** means within **5 miles** of the road. The detour to the station isn't added to the distance.
- **The truck starts with just enough fuel to reach its first stop**, which is the cheapest station within the first 25 miles (or, if there is none, the first station on the route). That fuel is billed at the first stop, so total gallons always equal distance ÷ 10 mpg.
- **A 15-mile reserve is kept in the tank.** No leg is planned longer than 485 miles. That covers the detour to a station up to 5 miles off the road, and the city-level station positions.
- **Any amount can be bought at a stop.** The truck arrives at the destination with an empty tank.
- **Each stop costs $10** in time off the road (configurable; it's only used to choose stops and isn't included in the reported fuel cost). Without it, the cheapest plan for New York → LA makes 16 stops, some to save under a cent a gallon. With it, the plan makes 7 stops for 1% more fuel cost.
- **Duplicate station IDs** in the CSV (597 of them) keep their **lowest price**. **Canadian stations** are excluded.

All of these are configurable through environment variables (see `.env.example`).

## Tests and code quality

```bash
pytest            # 131 tests, no network needed (the routing API is mocked)
ruff check .      # lint
ruff format .     # format
```

The tests cover the optimizer (hand-checked cases, plus 60 random routes compared with LP and MILP solvers), geometry, location parsing, data loading, the API's success and error paths, caching, and the routing client's failure modes. CI runs lint and tests on every push (`.github/workflows/ci.yml`).

## Project layout

```
fuelroute/                  Django project (settings from env, URLs)
routing/
  models.py                 Place, FuelStation
  views.py                  /api/route/ (DRF) and /map/
  services/
    planner.py              orchestration + route cache
    optimizer.py            fuel-stop optimizer (dynamic programming)
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

- Set `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`, and serve with gunicorn (`gunicorn fuelroute.wsgi -w 4`).
- **With more than one worker, set `REDIS_URL`.** Otherwise each worker has its own route cache and rate-limit counters.
- Set `DJANGO_BEHIND_PROXY=True` only behind nginx or a load balancer. Then the client IP and HTTPS come from `X-Forwarded-*`. Without a proxy those headers are ignored, so they can't be used to dodge the rate limit.
- `GET /api/health/` returns 200 only when station data is loaded and the cache (Redis) is reachable, for load-balancer health checks.
- Requests are rate limited (default `60/minute` per IP) to protect the free routing quota.

## Data credits

Fuel prices are from the assessment. City coordinates come from [GeoNames](https://www.geonames.org/) (CC BY 4.0) via the [`zipcodes`](https://github.com/seanpianka/zipcodes) package. Routing is by [OpenRouteService](https://openrouteservice.org/), and the map tiles are © OpenStreetMap contributors and © CARTO.
