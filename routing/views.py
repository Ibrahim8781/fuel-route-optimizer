from django.http import JsonResponse
from django.urls import reverse
from django.utils.http import urlencode
from rest_framework import serializers, status
from rest_framework.renderers import TemplateHTMLRenderer
from rest_framework.response import Response
from rest_framework.views import APIView, exception_handler

from routing.services.locations import LocationError
from routing.services.optimizer import FuelPlanError
from routing.services.ors import RoutingError
from routing.services.planner import plan_trip
from routing.services.stations import get_station_index


class TripQuerySerializer(serializers.Serializer):
    start = serializers.CharField(max_length=100, help_text='"City, ST" or "lat,lon"')
    finish = serializers.CharField(max_length=100, help_text='"City, ST" or "lat,lon"')
    stop_cost = serializers.FloatField(
        required=False, min_value=0, max_value=1000, help_text="USD per fuel stop; 0 = cheapest fuel"
    )


def error_body(code: str, message: str, **extra) -> dict:
    return {"error": {"code": code, "message": message, **extra}}


def api_exception_handler(exc, context):
    """Give framework errors (throttling, wrong method, ...) the same shape as ours."""
    response = exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "detail" in response.data:
        response.data = error_body(getattr(exc, "default_code", "error"), str(response.data["detail"]))
    return response


def run_plan(query) -> tuple[dict | None, dict | None, int]:
    """Validate the query and plan the trip. Returns (plan, error, http_status)."""
    serializer = TripQuerySerializer(data=query)
    if not serializer.is_valid():
        return None, error_body("invalid_request", "Invalid query parameters.", fields=serializer.errors), 400
    data = serializer.validated_data
    try:
        return plan_trip(data["start"], data["finish"], data.get("stop_cost")), None, 200
    except LocationError as exc:
        return None, error_body("invalid_location", str(exc)), 400
    except FuelPlanError as exc:
        return None, error_body("no_fuel_plan", str(exc)), 422
    except RoutingError as exc:
        return None, error_body("routing_failed", str(exc)), exc.status


class RoutePlanView(APIView):
    """GET /api/route/?start=Chicago, IL&finish=Denver, CO[&stop_cost=10]

    Returns the route geometry, the cheapest fuel stops (500-mile range) and the
    total fuel cost at 10 mpg, plus a link to the same trip on a map.
    """

    def get(self, request):
        plan, error, http_status = run_plan(request.query_params)
        if error:
            return Response(error, status=http_status)
        query = urlencode(
            {k: request.query_params[k] for k in ("start", "finish", "stop_cost") if k in request.query_params}
        )
        # Keep the large geometry last so the useful fields are at the top of the response.
        plan = {**plan, "map_url": request.build_absolute_uri(f"{reverse('route-map')}?{query}")}
        plan["route"] = plan.pop("route")
        return Response(plan, status=status.HTTP_200_OK)


class RouteMapView(APIView):
    """GET /map/?start=...&finish=... renders the same plan with Leaflet.

    A DRF view so it shares the API's rate limit: a cache miss here also calls the routing provider.
    """

    renderer_classes = [TemplateHTMLRenderer]
    template_name = "routing/map.html"

    def get(self, request):
        plan, error, http_status = run_plan(request.query_params)
        return Response({"plan": plan, "error": error}, status=http_status)


class HealthView(APIView):
    """GET /api/health/ for load balancers: the app is up and station data is loaded."""

    throttle_classes = []

    def get(self, request):
        stations = len(get_station_index())
        return Response(
            {"status": "ok" if stations else "no_data", "stations": stations}, status=200 if stations else 503
        )


def not_found(request, exception=None):
    return JsonResponse(
        error_body("not_found", "No such endpoint. Use GET /api/route/?start=...&finish=..."), status=404
    )


def server_error(request):
    return JsonResponse(error_body("server_error", "Something went wrong on our side."), status=500)
