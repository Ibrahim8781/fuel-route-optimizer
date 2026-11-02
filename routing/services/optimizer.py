"""Cheapest fuel plan along a fixed route (pure Python + numpy, no Django).

Model
-----
* The route runs from mile 0 to `route_miles`. Each candidate station has a mile
  marker and a price. The tank holds `max_range` miles of fuel (`max_range / mpg`
  gallons), and any amount can be bought at a stop.
* The truck starts with just enough fuel to reach its first stop: the cheapest
  station within the first `start_window` miles (or, if there is none, the
  first station on the route). That fuel is billed at the first stop, so
  total gallons always equal route_miles / mpg.
* Every stop also costs `stop_cost` dollars (time off the road). This stops the
  plan from making an extra stop to save a cent per gallon. With `stop_cost=0`
  the plan is simply the cheapest fuel.

Algorithm: dynamic programming over the fuel level
--------------------------------------------------
Fuel is measured in small units (`resolution` miles of range). Stations are
visited in route order, and we keep one array:

    best[f] = lowest cost to be at this station with f units of fuel

At a station whose fuel costs c per unit, filling up from g to f units costs
best[g] + c * (f - g) + stop_cost. For every f we need the best g < f, which
is a running (prefix) minimum of best[g] - c * g, so one pass is O(T) for a
tank of T units. Driving d units to the next station shifts the array by d.
Total: O(n * T) for n stations, a few milliseconds for a coast-to-coast route.
The result is exact for station positions rounded to `resolution`.
"""

from dataclasses import dataclass

import numpy as np


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
    stop_cost: float = 0.0,
    resolution: float = 0.5,
) -> FuelPlan:
    """Return the purchases that minimise fuel cost plus `stop_cost` per stop.

    `stations` must be sorted by mile marker.
    """
    if route_miles <= 0:
        return FuelPlan(purchases=[], total_gallons=0.0, total_cost=0.0)

    on_route = [i for i, s in enumerate(stations) if s.mile <= route_miles]
    if not on_route:
        raise FuelPlanError("No fuel station along this route, so the fuel cost can't be priced.")
    if stations[on_route[0]].mile > max_range:
        raise FuelPlanError(
            f"The first fuel station is at mile {stations[on_route[0]].mile:.0f}, beyond the {max_range:g}-mile range."
        )

    # First stop: cheapest station near the start (nearer one if prices tie).
    window = [i for i in on_route if stations[i].mile <= start_window] or on_route[:1]
    first = min(window, key=lambda i: (stations[i].price, stations[i].mile))
    usable = [i for i in on_route if i >= first]
    start_mile = stations[first].mile

    # Positions in fuel units, measured from the first stop.
    positions = [round((stations[i].mile - start_mile) / resolution) for i in usable]
    positions.append(round((route_miles - start_mile) / resolution))  # destination
    tank = round(max_range / resolution)
    levels = np.arange(tank + 1)
    # A tiny cost per stop even when stop_cost is 0, so equal-cost plans prefer fewer stops.
    per_stop = max(stop_cost, 1e-6)

    best = np.full(tank + 1, np.inf)
    best[0] = 0.0  # arrive at the first stop empty
    sources = []  # sources[k][f]: fuel level before buying at station k, for each level f after buying
    for k, i in enumerate(usable):
        unit_price = stations[i].price * resolution / mpg
        # Cheapest way to reach level f by buying here: min over g < f of (best[g] - unit_price * g).
        adjusted = best - unit_price * levels
        prefix_min = np.minimum.accumulate(adjusted)
        prefix_arg = np.maximum.accumulate(np.where(adjusted == prefix_min, levels, 0))
        buy_cost = np.full(tank + 1, np.inf)
        buy_cost[1:] = prefix_min[:-1] + unit_price * levels[1:] + per_stop

        buy = buy_cost < best
        after = np.where(buy, buy_cost, best)
        sources.append(np.where(buy, np.concatenate(([0], prefix_arg[:-1])), levels))

        # Drive to the next station (or the destination): fuel drops by the distance.
        distance = positions[k + 1] - positions[k]
        best = np.full(tank + 1, np.inf)
        if distance <= tank:
            best[: tank + 1 - distance] = after[distance:]
        if not np.isfinite(best).any():
            next_mile = stations[usable[k + 1]].mile if k + 1 < len(usable) else route_miles
            raise FuelPlanError(
                f"No fuel station between mile {stations[i].mile:.0f} and mile {next_mile:.0f}; "
                f"the gap exceeds the {max_range:g}-mile range."
            )

    # Walk back from the destination (arriving with the least fuel) to recover the purchases.
    fuel = int(np.argmin(best))
    bought: dict[int, int] = {}
    for k in range(len(usable) - 1, -1, -1):
        level_after = fuel + positions[k + 1] - positions[k]
        fuel = int(sources[k][level_after])
        if level_after > fuel:
            bought[usable[k]] = level_after - fuel

    gallons_at = {i: units * resolution / mpg for i, units in bought.items()}
    gallons_at[first] = gallons_at.get(first, 0.0) + start_mile / mpg
    # Positions were rounded to `resolution`; the last stop absorbs the (< 0.05 gal) difference
    # so total gallons equal route_miles / mpg exactly.
    gallons_at[max(gallons_at)] += route_miles / mpg - sum(gallons_at.values())

    purchases = [
        Purchase(index=i, gallons=gallons, cost=gallons * stations[i].price)
        for i, gallons in sorted(gallons_at.items())
        if gallons > 1e-9
    ]
    return FuelPlan(
        purchases=purchases,
        total_gallons=sum(p.gallons for p in purchases),
        total_cost=sum(p.cost for p in purchases),
    )
