"""Fuel optimizer tests. Small cases are worked out by hand; random cases are checked against an LP solver."""

import random

import numpy as np
import pytest
from scipy.optimize import linprog

from routing.services.optimizer import Candidate, FuelPlanError, plan_fuel_stops


def plan(stations, route_miles, **kwargs):
    candidates = [Candidate(mile=m, price=p) for m, p in stations]
    return plan_fuel_stops(candidates, route_miles, **kwargs)


def stops(result):
    return [(p.index, round(p.gallons, 6)) for p in result.purchases]


def test_short_route_single_stop():
    result = plan([(0, 3.00), (100, 2.00)], route_miles=80)
    # Destination is reached before the cheaper station: buy 8 gallons at the start.
    assert stops(result) == [(0, 8.0)]
    assert result.total_cost == pytest.approx(24.0)


def test_buys_only_enough_to_reach_a_cheaper_station():
    result = plan([(0, 4.00), (100, 3.00)], route_miles=300)
    # 10 gal at $4 to reach mile 100, then 20 gal at $3 to finish.
    assert stops(result) == [(0, 10.0), (1, 20.0)]
    assert result.total_cost == pytest.approx(10 * 4 + 20 * 3)


def test_fills_up_when_current_station_is_cheapest_in_range():
    result = plan([(0, 3.00), (400, 4.00), (700, 3.50)], route_miles=900)
    # Fill 50 gal at $3. Cheapest in range is mile 400 ($4; mile 700 is out of range), arrive with 10 gal.
    # At mile 400, the $3.50 station at 700 is cheaper: buy 30 - 10 = 20 gal. Then 20 gal at $3.50.
    assert stops(result) == [(0, 50.0), (1, 20.0), (2, 20.0)]
    assert result.total_cost == pytest.approx(50 * 3 + 20 * 4 + 20 * 3.5)


def test_first_stop_is_cheapest_in_start_window_and_bills_the_approach():
    result = plan([(2, 3.50), (10, 3.00), (60, 3.40)], route_miles=200, start_window=25)
    # Mile 10 is the cheapest within 25 miles; the 10 miles driven to it are billed there.
    assert stops(result) == [(1, 20.0)]
    assert result.total_gallons == pytest.approx(20.0)


def test_skips_stations_it_does_not_need():
    result = plan([(0, 3.00), (50, 3.50), (120, 3.20)], route_miles=450)
    assert stops(result) == [(0, 45.0)]


def test_no_station_near_start():
    with pytest.raises(FuelPlanError, match="within 25 miles of the start"):
        plan([(40, 3.00)], route_miles=300)


def test_gap_longer_than_range():
    with pytest.raises(FuelPlanError, match="between mile 0 and mile 600"):
        plan([(0, 3.00), (600, 3.00)], route_miles=900)


def test_zero_length_route():
    result = plan([], route_miles=0)
    assert result.purchases == [] and result.total_cost == 0


def lp_optimal_cost(stations, route_miles, max_range=500.0, mpg=10.0, start_window=25.0):
    """Exact optimum with the same start rule, solved as a linear program."""
    window = [i for i, (m, _) in enumerate(stations) if m <= start_window]
    first = min(window, key=lambda i: (stations[i][1], stations[i][0]))
    usable = stations[first:]
    base = usable[0][0]
    n, tank = len(usable), max_range / mpg
    prices = [p for _, p in usable]

    a_ub, b_ub = [], []
    for k in range(n):
        used = (usable[k][0] - base) / mpg
        a_ub.append([-1.0 if i < k else 0.0 for i in range(n)])  # arrive with fuel >= 0
        b_ub.append(-used)
        a_ub.append([1.0 if i <= k else 0.0 for i in range(n)])  # tank never overfills
        b_ub.append(tank + used)
    result = linprog(
        prices, A_ub=a_ub, b_ub=b_ub, A_eq=[[1.0] * n], b_eq=[(route_miles - base) / mpg], bounds=(0, None)
    )
    assert result.success
    return result.fun + stations[first][1] * base / mpg


@pytest.mark.parametrize("seed", range(40))
def test_greedy_matches_linear_program_optimum(seed):
    rng = random.Random(seed)
    route_miles = rng.uniform(300, 3000)
    miles = sorted([rng.uniform(0, 20)] + [rng.uniform(0, route_miles) for _ in range(rng.randint(5, 60))])
    # Make sure no gap exceeds the range so every instance is feasible.
    filled = [miles[0]]
    for m in miles[1:] + [route_miles]:
        while m - filled[-1] > 450:
            filled.append(filled[-1] + 400)
        if m < route_miles:
            filled.append(m)
    stations = [(m, round(rng.uniform(2.7, 4.5), 3)) for m in filled]

    greedy = plan(stations, route_miles)
    assert greedy.total_cost == pytest.approx(lp_optimal_cost(stations, route_miles), rel=1e-6)
    assert greedy.total_gallons == pytest.approx(route_miles / 10)
    assert np.all([p.gallons > 0 for p in greedy.purchases])
