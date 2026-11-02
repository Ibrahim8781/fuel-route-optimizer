"""Cheapest fuel plan along a fixed route (pure Python, no Django).

Model
-----
* The route is a line from mile 0 to `route_miles`. Each candidate station has
  a mile marker and a price. The tank holds `max_range / mpg` gallons.
* The truck starts with an empty tank, so the first fill-up is the cheapest
  station within the first `start_window` miles (or, if there is none, the
  first station on the route). The miles driven to get there are billed at
  that station, so total gallons always equal route_miles / mpg.
* The truck may buy any amount at a stop and should arrive with an empty tank.

Greedy rule (optimal for this model)
------------------------------------
At each stop, look at the stations reachable on a full tank:

1. If a cheaper station is reachable, buy only enough to get to the nearest
   such station. Fuel bought here would cost more than fuel bought there.
2. Otherwise, if the destination is reachable, buy just enough to finish.
3. Otherwise this is the cheapest fuel in range: fill the tank, then drive to
   the cheapest station in range (the farthest one if prices tie).

Exchange argument: any gallon burned between this stop and the next cheaper
station must be bought here or at a more expensive station, so buying it here
is never worse. Each step moves forward, so the loop runs O(n x k) for n
stations and k stations within range (n is a few hundred on a long route).
"""

from bisect import bisect_right
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    mile: float  # position along the route
    price: float  # USD per gallon


@dataclass(frozen=True)
class Purchase:
    index: int  # position in the stations list
    gallons: float
    cost: float


@dataclass(frozen=True)
class FuelPlan:
    purchases: list[Purchase]
    total_gallons: float
    total_cost: float


class FuelPlanError(Exception):
    """The route cannot be completed with the stations available."""


def plan_fuel_stops(
    stations: list[Candidate],
    route_miles: float,
    *,
    max_range: float = 500.0,
    mpg: float = 10.0,
    start_window: float = 25.0,
) -> FuelPlan:
    """Return the purchases that minimise fuel cost.

    `stations` must be sorted by mile marker.
    """
    if route_miles <= 0:
        return FuelPlan(purchases=[], total_gallons=0.0, total_cost=0.0)

    on_route = [i for i, s in enumerate(stations) if s.mile <= route_miles]
    if not on_route or stations[on_route[0]].mile > max_range:
        raise FuelPlanError(f"No fuel station within {max_range:g} miles of the start.")
    first_window = [i for i in on_route if stations[i].mile <= start_window] or on_route[:1]

    tank = max_range / mpg
    miles = [s.mile for s in stations]
    gallons_at: dict[int, float] = {}

    def buy(index: int, gallons: float) -> None:
        if gallons > 1e-9:
            gallons_at[index] = gallons_at.get(index, 0.0) + gallons

    # First fill-up: cheapest station near the start (nearer one if prices tie).
    current = min(first_window, key=lambda i: (stations[i].price, stations[i].mile))
    buy(current, stations[current].mile / mpg)  # fuel used to reach it
    fuel = 0.0  # gallons in the tank on arrival at `current`

    while True:
        here = stations[current]
        reach = here.mile + max_range
        # Stations ahead that a full tank can reach (stations past the destination are useless).
        in_range = range(current + 1, bisect_right(miles, min(reach, route_miles)))

        cheaper = next((i for i in in_range if stations[i].price < here.price), None)
        if cheaper is not None:
            needed = (stations[cheaper].mile - here.mile) / mpg
            buy(current, needed - fuel)
            fuel = max(fuel - needed, 0.0)
            current = cheaper
            continue

        if reach >= route_miles:
            buy(current, (route_miles - here.mile) / mpg - fuel)
            break

        if not in_range:
            next_mile = next((s.mile for s in stations[current + 1 :]), route_miles)
            raise FuelPlanError(
                f"No fuel station between mile {here.mile:.0f} and mile {next_mile:.0f}; "
                f"the gap exceeds the {max_range:g}-mile range."
            )

        buy(current, tank - fuel)
        cheapest = min(in_range, key=lambda i: (stations[i].price, -stations[i].mile))
        fuel = tank - (stations[cheapest].mile - here.mile) / mpg
        current = cheapest

    purchases = [Purchase(index=i, gallons=g, cost=g * stations[i].price) for i, g in sorted(gallons_at.items())]
    return FuelPlan(
        purchases=purchases,
        total_gallons=sum(p.gallons for p in purchases),
        total_cost=sum(p.cost for p in purchases),
    )
