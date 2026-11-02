from django.urls import path

from routing.views import RoutePlanView, route_map

urlpatterns = [
    path("api/route/", RoutePlanView.as_view(), name="route-plan"),
    path("map/", route_map, name="route-map"),
]
