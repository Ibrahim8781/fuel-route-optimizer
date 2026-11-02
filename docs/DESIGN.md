# Design and walkthrough

This document goes in the order I approached the problem: requirements → data → decisions → architecture → algorithm → code → non-functional concerns.

## 1. Requirements

| Requirement | How it is met |
|---|---|
| Start and finish inside the USA | `City, ST`, `City, State` or `lat,lon`, resolved offline and checked against US bounds |
| Map of the route | GeoJSON `route` in the response, plus a Leaflet map at `map_url` |
| Cost-effective fuel stops | Greedy algorithm that is optimal for a fixed route (section 5) |
| 500-mile range, multiple stops | Tank = 500 mi ÷ 10 mpg = 50 gal; the plan never exceeds it |
| Total fuel cost at 10 mpg | `summary.total_fuel_cost_usd`; total gallons = distance ÷ 10 |
| Use the provided price file | Loaded once by `manage.py load_data` |
| Free map/routing API | OpenRouteService (`driving-hgv` truck profile); OpenStreetMap tiles |
| Latest stable Django | Django 6.1 + Django REST framework |
| Fast responses | ~35 ms server-side work per new trip, ~6 ms when cached |
| Few routing API calls (1 ideal) | **Exactly 1** per new trip, **0** for repeated trips and for the map |

## 2. What the data told me

I inspected the CSV before designing anything:

- **8,151 rows but only 6,738 unique station IDs.** 597 IDs appear more than once with different prices. I keep the **lowest** price, which is what a driver would pay.
- **It includes Canadian provinces** (AB, BC, ON, QC…). These are excluded because the task is USA only. That leaves **6,626 US stations**.
- **There are no coordinates.** Addresses are highway exits such as `I-44, EXIT 283 & US-69`, which geocoding APIs handle poorly.
- **But there are only 4,275 distinct cities.** That makes offline, city-level geocoding practical.

## 3. Key decisions and trade-offs

| Decision | Why | Trade-off |
|---|---|---|
| **Geocode stations offline to city level** (bundled US places table) | No API calls; 100% of US stations matched; runs in ~3 s once | Position is the city centre, not the exact exit |
| **OpenRouteService `driving-hgv`** | Free, truck-aware routing, returns geometry and distance in one call | Free quota (2,000 routes/day), so results are cached |
| **One routing call, cached by coordinates** | Meets the "1 call" requirement; repeated trips and the map cost 0 calls | Cache must be shared across workers in production (Redis supported) |
| **Resample the route to 1 point per mile** | Uniform accuracy (≤ 0.5 mi) and a small payload (~60 KB for a coast-to-coast trip) | Curves are slightly smoothed on the map |
| **KD-tree corridor search** | Finds stations within 5 mi of the route in O(S log R) | Uses a radius, not exact distance to each road segment |
| **Greedy fuel algorithm** | Provably optimal for a fixed route, simple, O(n·k) | Ignores time spent per stop |
| **Stations held in memory; SQLite** | The data is static and small (6.6k rows), so the DB isn't on the request path | Restart the server after reloading the data |

## 4. Architecture

```mermaid
flowchart LR
    C[Client / Postman] -->|GET /api/route/| V[RoutePlanView<br/>DRF, validation]
    B[Browser] -->|GET /map/| M[route_map<br/>Leaflet page]
    V --> P[planner.plan_trip]
    M --> P
    P --> L[locations<br/>City, ST to lat/lon]
    L --> DB[(SQLite<br/>Place, FuelStation)]
    P --> RC{Route cache}
    RC -->|miss: 1 call| ORS[OpenRouteService<br/>directions API]
    P --> SI[Station index<br/>in memory, numpy]
    SI -.loaded once.-> DB
    P --> G[geo<br/>KD-tree corridor]
    P --> O[optimizer<br/>greedy, pure Python]
    LD[manage.py load_data] -->|one-time| DB
```

Request flow:

```mermaid
sequenceDiagram
    participant Client
    participant API as Django API
    participant Cache
    participant ORS as OpenRouteService
    Client->>API: GET /api/route/?start=...&finish=...
    API->>API: resolve both locations (offline)
    API->>Cache: route for these coordinates?
    alt cache miss
        API->>ORS: POST /v2/directions/driving-hgv (1 call)
        ORS-->>API: geometry + distance
        API->>Cache: store resampled route (24 h)
    end
    API->>API: KD-tree: stations within 5 mi + mile markers
    API->>API: greedy optimizer: stops + cost
    API-->>Client: JSON (summary, stops, map_url, route)
```

## 5. The fuel algorithm

Stations near the route are sorted by **mile marker**. The tank holds 50 gallons (500 miles). At each stop:

1. **A cheaper station is reachable:** buy only enough to reach the **nearest** cheaper station.
2. **Otherwise, the destination is reachable:** buy just enough to finish.
3. **Otherwise, this is the cheapest fuel in range:** fill the tank, then drive to the **cheapest** station in range.

**Why it's optimal:** every gallon burned between here and the next cheaper station has to be bought here or at a more expensive station, so buying it here is never worse. When nothing cheaper is in range, filling up here covers as many miles as possible at the lowest available price. The test suite checks this by comparing the result with an exact linear-program solver on 40 random routes.

**Start:** the truck starts empty, so its first fill-up is the cheapest station within 25 miles of the start. If there is none, it's the first station on the route. The fuel used to reach it is billed at that station.

**Worked example** (from the tests): stations at mile 0 ($3.00), 400 ($4.00) and 700 ($3.50); the trip is 900 miles.
- Mile 0 is the cheapest fuel in range: fill 50 gal and go to the cheapest in range, mile 400, arriving with 10 gal.
- At mile 400 the $3.50 station is reachable and cheaper: buy 20 gal, just enough to reach it.
- At mile 700 the destination is reachable: buy 20 gal.
- Total: 50×3 + 20×4 + 20×3.5 = **$300** for 90 gallons.

**Complexity:** finding the stations is O(R log R + S log R), for R route points and S stations. The optimizer is O(n·k), for n stations on the route and k within range, using a binary search for the range. On a 2,650-mile test route, the whole request takes about 35 ms of compute.

Compared with a simpler "always fill up at the cheapest reachable station" strategy, on the same 2,650-mile test route this algorithm was **cheaper ($797 vs $808) and made fewer stops (10 vs 12)**.

## 6. Code tour

| File | What it does |
|---|---|
| `routing/views.py` | DRF endpoint, input validation and error mapping; the map view |
| `routing/services/planner.py` | Orchestrates one trip; caches the route |
| `routing/services/optimizer.py` | The greedy algorithm; no Django, easy to test |
| `routing/services/geo.py` | Haversine, route resampling, KD-tree corridor search |
| `routing/services/ors.py` | OpenRouteService client: one POST, clear errors |
| `routing/services/locations.py` | Parses `City, ST` / `lat,lon`; US bounds check |
| `routing/services/stations.py` | Loads all stations into numpy arrays once per process |
| `routing/management/commands/load_data.py` | Filters, deduplicates and geocodes the CSV, atomically |
| `routing/templates/routing/map.html` | Leaflet map with the route and numbered stops |

## 7. Non-functional concerns

- **Performance:** no database access on the hot path (stations are in memory), vectorised numpy and a KD-tree, a 1-point-per-mile route, and a route cache. The heavy geometry comes last in the response.
- **External API usage:** 1 call per new trip, 0 when cached. Per-IP rate limiting (default 60/min) protects the free quota.
- **Consistent errors:** every error, including throttling and wrong HTTP method, returns `{"error": {"code", "message"}}`.
- **Reliability:** 20 s timeout on the routing call. Provider errors map to clear HTTP statuses: 422 when no route exists, 503 for quota or key problems, 504 on timeout.
- **Security:** secrets come from the environment (`.env` is git-ignored), and the app refuses to start without a secret key when DEBUG is off. Input lengths are validated, map popups escape station data, and the app has a minimal surface (no admin, sessions or auth).
- **Observability:** every routing call is logged with its timing. Each response includes `meta` (routing API calls, cache hit, stations considered, elapsed ms).
- **Testability:** 91 tests with no network. The core algorithm is a pure function.
- **Scalability:** the web tier is stateless; with `REDIS_URL` set, all workers share the route cache. See the next steps for growing the data.

## 8. Limitations and next steps

- **City-level station positions.** Using exit numbers or a geocoded address list would allow a tighter corridor and real detour distances.
- **No cost per stop.** The plan may include small "bridge" top-ups. A per-stop time or cost penalty would trade a few cents for fewer stops.
- **Larger datasets:** move stations to PostGIS and use `ST_DWithin` along the route line.
- **Prices change:** schedule `load_data` against a live price feed.
