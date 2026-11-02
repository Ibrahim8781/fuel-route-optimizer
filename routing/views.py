from django.shortcuts import render
from django.urls import reverse
from django.utils.http import urlencode
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from routing.services.locations import LocationError
from routing.services.optimizer import FuelPlanError
from routing.services.ors import RoutingError
from routing.services.planner import plan_trip


class TripQuerySerializer(serializers.Serializer):
    start = serializers.CharField(max_length=100, help_text='"City, ST" or "lat,lon"')
    finish = serializers.CharField(max_length=100, help_text='"City, ST" or "lat,lon"')


def error_body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def run_plan(query) -> tuple[dict | None, dict | None, int]:
    """Validate the query and plan the trip. Returns (plan, error, http_status)."""
    serializer = TripQuerySerializer(data=query)
    if not serializer.is_valid():
        return None, error_body("invalid_request", "start and finish are required.", fields=serializer.errors), 400
    data = serializer.validated_data
    try:
        return plan_trip(data["start"], data["finish"]), None, 200
    except LocationError as exc:
        return None, error_body("invalid_location", str(exc)), 400
    except FuelPlanError as exc:
        return None, error_body("no_fuel_plan", str(exc)), 422
    except RoutingError as exc:
        return None, error_body("routing_failed", str(exc)), exc.status


class RoutePlanView(APIView):
    """GET /api/route/?start=Chicago, IL&finish=Denver, CO

    Returns the route geometry, the cheapest fuel stops (500-mile range) and the
    total fuel cost at 10 mpg, plus a link to the same trip on a map.
    """

    def get(self, request):
        plan, error, http_status = run_plan(request.query_params)
        if error:
            return Response(error, status=http_status)
        query = urlencode({"start": request.query_params["start"], "finish": request.query_params["finish"]})
        # Keep the large geometry last so the useful fields are at the top of the response.
        plan = {**plan, "map_url": request.build_absolute_uri(f"{reverse('route-map')}?{query}")}
        plan["route"] = plan.pop("route")
        return Response(plan, status=status.HTTP_200_OK)


def route_map(request):
    """GET /map/?start=...&finish=... renders the same plan with Leaflet (uses the cached route)."""
    plan, error, http_status = run_plan(request.GET)
    return render(request, "routing/map.html", {"plan": plan, "error": error}, status=http_status)
