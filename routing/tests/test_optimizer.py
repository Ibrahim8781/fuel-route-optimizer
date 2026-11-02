"""Fuel optimizer tests.

Small cases are worked out by hand. Random cases are checked against exact
solvers: a linear program for the pure-cost case, and a mixed-integer program
when every stop has a cost.
"""

import random

import numpy as np
import pytest
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

from routing.services.optimizer import Candidate, FuelPlanError, plan_fuel_stops


def plan(stations, route_miles, **kwargs):
    candidates = [Candidate(mile=m, price=p) for m, p in stations]
    return plan_fuel_stops(candidates, route_miles, **kwargs)


def stops(result):
    return [(p.index, round(p.gallons, 6)) for p in result.purchases]


# --- Hand-checked cases (stop_cost=0: cheapest fuel) --------------------------


def test_short_route_single_stop():
    result = plan([(0, 3.00), (100, 2.00)], route_miles=80)
    # The cheaper station is past the destination, so it is ignored.
    assert stops(result) == [(0, 8.0)]
    assert result.total_cost == pytest.approx(24.0)


def test_buys_only_enough_to_reach_a_cheaper_station():
    result = plan([(0, 4.00), (100, 3.00)], route_miles=300)
    # 10 gal at $4 to reach mile 100, then 20 gal at $3 to finish.
    assert stops(result) == [(0, 10.0), (1, 20.0)]
    assert result.total_cost == pytest.approx(10 * 4 + 20 * 3)


def test_fills_up_when_current_station_is_cheapest_in_range():
    result = plan([(0, 3.00), (400, 4.00), (700, 3.50)], route_miles=900)
    # Fill 50 gal at $3 and arrive at mile 400 with 10 gal; buy 20 gal at $4 to reach the $3.50
    # station; buy 20 gal there to finish.
    assert stops(result) == [(0, 50.0), (1, 20.0), (2, 20.0)]
    assert result.total_cost == pytest.approx(50 * 3 + 20 * 4 + 20 * 3.5)


def test_first_stop_is_cheapest_in_start_window_and_bills_the_approach():
    result = plan([(2, 3.50), (10, 3.00), (60, 3.40)], route_miles=200, start_window=25)
    # Mile 10 is the cheapest within 25 miles; the 10 miles driven to it are billed there.
    assert stops(result) == [(1, 20.0)]
    assert result.total_gallons == pytest.approx(20.0)


def test_no_station_in_start_window_uses_first_station_on_route():
    result = plan([(40, 3.50), (60, 3.00)], route_miles=300)
    # Nothing within 25 miles: the truck starts with enough fuel to reach mile 40 and is billed for it there.
    assert stops(result) == [(0, 6.0), (1, 24.0)]
    assert result.total_gallons == pytest.approx(30.0)


def test_skips_stations_it_does_not_need():
    result = plan([(0, 3.00), (50, 3.50), (120, 3.20)], route_miles=450)
    assert stops(result) == [(0, 45.0)]


# --- Cost per stop -----------------------------------------------------------


def test_stop_cost_avoids_stopping_to_save_a_cent():
    stations = [(0, 3.00), (30, 2.99)]
    # Cheapest fuel: 3 gal at $3.00 to reach the $2.99 station, then 37 gal there ($119.63 vs $120.00).
    assert stops(plan(stations, 400)) == [(0, 3.0), (1, 37.0)]
    # With $10 per stop, one stop is better: 40 gal at $3.00.
    assert stops(plan(stations, 400, stop_cost=10)) == [(0, 40.0)]


def test_stop_cost_never_breaks_the_range_limit():
    stations = [(0, 3.00), (450, 3.00), (900, 3.00)]
    result = plan(stations, 1200, stop_cost=1000)
    assert [p.index for p in result.purchases] == [0, 1, 2]  # every stop is still required


# --- Errors and edge cases ----------------------------------------------------


def test_no_station_reachable_from_start():
    with pytest.raises(FuelPlanError, match="within 500 miles of the start"):
        plan([(600, 3.00)], route_miles=900)
    with pytest.raises(FuelPlanError, match="within 500 miles of the start"):
        plan([], route_miles=300)


def test_gap_longer_than_range():
    with pytest.raises(FuelPlanError, match="between mile 0 and mile 600"):
        plan([(0, 3.00), (600, 3.00)], route_miles=900)


def test_destination_at_the_first_stop():
    result = plan([(10, 3.00)], route_miles=10)
    assert stops(result) == [(0, 1.0)]  # only the approach is billed


def test_zero_length_route():
    result = plan([], route_miles=0)
    assert result.purchases == [] and result.total_cost == 0


# --- Random routes against exact solvers ---------------------------------------


def random_instance(seed):
    """A feasible random route; positions on the optimizer's 0.5-mile grid so results compare exactly."""
    rng = random.Random(seed)
    route_miles = rng.randint(600, 6000) / 2
    miles = sorted(
        [rng.randint(0, 40) / 2] + [rng.randint(0, int(route_miles * 2)) / 2 for _ in range(rng.randint(5, 40))]
    )
    filled = [miles[0]]
    for mile in miles[1:] + [route_miles]:
        while mile - filled[-1] > 450:  # keep every gap within range
            filled.append(filled[-1] + 400)
        if mile < route_miles:
            filled.append(mile)
    return [(m, round(rng.uniform(2.7, 4.5), 3)) for m in filled], route_miles


def exact_cost(stations, route_miles, stop_cost=0.0, max_range=500.0, mpg=10.0, start_window=25.0):
    """Optimum with the same start rule: LP when stops are free, MILP when each stop costs `stop_cost`."""
    window = [i for i, (m, _) in enumerate(stations) if m <= start_window] or [0]
    first = min(window, key=lambda i: (stations[i][1], stations[i][0]))
    usable = stations[first:]
    base = usable[0][0]
    n, tank = len(usable), max_range / mpg
    prices = np.array([p for _, p in usable])

    rows, upper = [], []
    for k in range(n):
        used = (usable[k][0] - base) / mpg
        rows.append([-1.0 if i < k else 0.0 for i in range(n)])  # arrive with fuel >= 0
        upper.append(-used)
        rows.append([1.0 if i <= k else 0.0 for i in range(n)])  # tank never overfills
        upper.append(tank + used)
    need = (route_miles - base) / mpg
    approach = usable[0][1] * base / mpg

    if stop_cost == 0:
        result = linprog(prices, A_ub=rows, b_ub=upper, A_eq=[[1.0] * n], b_eq=[need], bounds=(0, None))
        assert result.success
        return result.fun + approach

    # Variables: gallons x_0..x_{n-1}, then stop flags y_0..y_{n-1}; x_i <= tank * y_i.
    a = np.hstack([np.array(rows), np.zeros((2 * n, n))])
    link = np.hstack([np.eye(n), -tank * np.eye(n)])
    total = np.hstack([np.ones((1, n)), np.zeros((1, n))])
    constraints = [
        LinearConstraint(a, -np.inf, upper),
        LinearConstraint(link, -np.inf, 0),
        LinearConstraint(total, need, need),
    ]
    cost = np.concatenate([prices, np.full(n, stop_cost)])
    integrality = np.concatenate([np.zeros(n), np.ones(n)])
    bounds = Bounds(np.zeros(2 * n), np.concatenate([np.full(n, np.inf), np.ones(n)]))
    result = milp(cost, constraints=constraints, integrality=integrality, bounds=bounds)
    assert result.success
    # The first stop always happens (its approach is billed there), so add back its stop cost if unused.
    stops_used = round(result.x[n:].sum())
    return result.fun + approach + (stop_cost if base > 0 and stops_used == 0 else 0)


@pytest.mark.parametrize("seed", range(30))
def test_matches_linear_program_when_stops_are_free(seed):
    stations, route_miles = random_instance(seed)
    result = plan(stations, route_miles)
    assert result.total_cost == pytest.approx(exact_cost(stations, route_miles), abs=1e-4)
    assert result.total_gallons == pytest.approx(route_miles / 10)


@pytest.mark.parametrize("seed", range(30))
def test_matches_integer_program_with_stop_cost(seed):
    stations, route_miles = random_instance(seed)
    stop_cost = 10.0
    result = plan(stations, route_miles, stop_cost=stop_cost)
    objective = result.total_cost + stop_cost * len(result.purchases)
    assert objective == pytest.approx(exact_cost(stations, route_miles, stop_cost=stop_cost), abs=1e-3)
    assert result.total_gallons == pytest.approx(route_miles / 10)
    assert all(p.gallons > 0 for p in result.purchases)
